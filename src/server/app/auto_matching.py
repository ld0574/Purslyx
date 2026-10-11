"""事件驱动的自动匹配。账号锁、唯一约束、冻结输入和原任务队列共同保护扣次。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .budget import ensure_price_version, metric_date
from .config import settings
from .errors import DomainError, NotFoundError
from .models import (
    Account,
    Analysis,
    AutoMatchingConfig,
    AutoMatchJob,
    Document,
    DocumentVersion,
    JobPoolItem,
    Preference,
    PreferenceVersion,
    SiteBudgetBucket,
    Task,
    UsageBalance,
)
from .pricing import token_upper_bound
from .request_context import request_context
from .services import _lock_account_write_lane, available_count, payload_hash, shanghai_date, utcnow

AUTOMATION_VERSION = "auto-matching-v1"
PARSE_OUTPUT_LIMIT = 4000
MATCH_OUTPUT_LIMIT = 8000
LOGGER = logging.getLogger("purslyx.auto_matching")
BLOCK_MESSAGES = {
    "USAGE_INSUFFICIENT": "分析次数不足，已保留待处理；补充次数后自动继续",
    "AUTO_DAILY_LIMIT": "已达今日自动处理上限，明天继续",
    "TASK_QUEUE_LIMIT_REACHED": "任务队列繁忙，空闲后自动继续",
    "AUTO_PAUSED": "自动处理已暂停，待处理岗位保留",
    "AUTO_CONFIG_INVALID": "默认简历或岗位期望不可用，请重新设置",
    "AUTO_SOURCE_DELETED": "来源已删除，不再自动处理",
    "AUTO_MANUAL_ANALYSIS": "该岗位已有手动分析，不再自动重复扣次",
    "BUDGET_LIMIT_REACHED": "全站模型预算暂不足，已保留待处理",
    "BUDGET_CONCURRENCY_LIMIT": "模型调用繁忙，空闲后自动继续",
    "MODEL_PRICE_UNKNOWN": "模型价格尚未配置，已保留待处理",
}


def config_for(db: Session, account_id: int) -> AutoMatchingConfig | None:
    return db.scalar(select(AutoMatchingConfig).where(AutoMatchingConfig.account_id == account_id))


def _live_version(
    db: Session, account_id: int, version_id: int | None, document_type: str
) -> DocumentVersion:
    version = db.get(DocumentVersion, version_id, populate_existing=True) if version_id else None
    document = db.get(Document, version.document_id, populate_existing=True) if version else None
    if (
        version is None
        or version.account_id != account_id
        or version.deleted_at is not None
        or document is None
        or document.account_id != account_id
        or document.deleted_at is not None
        or document.document_type
        not in ({"job_description", "job"} if document_type == "job" else {document_type})
    ):
        raise DomainError("AUTO_SOURCE_DELETED", "自动匹配来源已不可用", 409)
    return version


def selected_inputs(
    db: Session, account_id: int, resume_document_id: int | None, preference_ids: list[str]
) -> tuple[DocumentVersion, list[PreferenceVersion]]:
    document = db.get(Document, resume_document_id) if resume_document_id else None
    if (
        document is None
        or document.account_id != account_id
        or document.deleted_at is not None
        or document.document_type != "resume"
        or document.subject_type != "self_resume"
        or document.status not in {"confirmed", "available"}
    ):
        raise DomainError("AUTO_CONFIG_INVALID", "请选择自己的已确认简历", 422)
    resume = db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.account_id == account_id,
            DocumentVersion.deleted_at.is_(None),
        )
        .order_by(DocumentVersion.version_no.desc())
    )
    if resume is None or not preference_ids or len(preference_ids) != len(set(preference_ids)):
        raise DomainError("AUTO_CONFIG_INVALID", "请确认简历并选择至少一套有效岗位期望", 422)
    versions = []
    for public_id in preference_ids:
        profile = db.scalar(
            select(Preference).where(
                Preference.account_id == account_id,
                Preference.public_id == public_id,
                Preference.deleted_at.is_(None),
                Preference.status == "active",
                Preference.subject_document_id.is_(None),
            )
        )
        version = (
            db.scalar(
                select(PreferenceVersion)
                .where(
                    PreferenceVersion.preference_id == profile.id,
                    PreferenceVersion.account_id == account_id,
                    PreferenceVersion.deleted_at.is_(None),
                )
                .order_by(PreferenceVersion.version_no.desc())
            )
            if profile
            else None
        )
        if version is None:
            raise DomainError("AUTO_CONFIG_INVALID", "选中的岗位期望已不可用，请重新选择", 422)
        versions.append(version)
    return resume, versions


def config_view(db: Session, account_id: int) -> dict[str, Any]:
    config = config_for(db, account_id)
    document = (
        db.get(Document, config.resume_document_id)
        if config and config.resume_document_id
        else None
    )
    valid = False
    if config and config.confirmed_at:
        try:
            selected_inputs(db, account_id, config.resume_document_id, config.preference_ids)
            valid = True
        except DomainError:
            pass
    balance = db.scalar(
        select(UsageBalance).where(
            UsageBalance.account_id == account_id, UsageBalance.feature == "analysis"
        )
    )
    used = (
        db.scalar(
            select(func.count(AutoMatchJob.id)).where(
                AutoMatchJob.account_id == account_id,
                AutoMatchJob.scheduled_date == shanghai_date(),
            )
        )
        or 0
    )
    return {
        "enabled": bool(config and config.enabled),
        "valid": valid,
        "resume_document_id": document.public_id
        if document and document.deleted_at is None
        else None,
        "preference_ids": config.preference_ids if config else [],
        "daily_limit": config.daily_limit if config else 10,
        "revision": config.revision if config else 0,
        "confirmed_at": config.confirmed_at.isoformat() if config and config.confirmed_at else None,
        "today_scheduled": used,
        "available_analysis": available_count(balance) if balance else 0,
        "timezone": "Asia/Shanghai",
        "version": AUTOMATION_VERSION,
    }


def register_capture(
    db: Session, account: Account, pool: JobPoolItem, request_id: str | None
) -> AutoMatchJob | None:
    """仅登记授权之后的新抓取；开启设置不会擅自回扫历史岗位。"""
    config = config_for(db, account.id)
    if not config or not config.enabled or not config.confirmed_at or account.status != "active":
        return None
    source_key = payload_hash({"platform": pool.platform, "source_hash": pool.source_url_hash})
    existing = db.scalar(
        select(AutoMatchJob).where(
            AutoMatchJob.account_id == account.id, AutoMatchJob.source_key == source_key
        )
    )
    if existing:
        return existing
    row = AutoMatchJob(
        account_id=account.id,
        config_id=config.id,
        config_revision=config.revision,
        job_pool_item_id=pool.id,
        source_key=source_key,
        request_id=request_id,
    )
    try:
        resume, preferences = selected_inputs(
            db, account.id, config.resume_document_id, config.preference_ids
        )
        row.resume_version_id = resume.id
        row.preference_version_ids = [version.public_id for version in preferences]
    except DomainError:
        row.blocked_code = "AUTO_CONFIG_INVALID"
    db.add(row)
    db.flush()
    return row


def job_inputs(
    db: Session, row: AutoMatchJob
) -> tuple[JobPoolItem, DocumentVersion, DocumentVersion, list[PreferenceVersion]]:
    pool = db.get(JobPoolItem, row.job_pool_item_id, populate_existing=True)
    if (
        row.deleted_at is not None
        or pool is None
        or pool.account_id != row.account_id
        or pool.deleted_at is not None
    ):
        raise DomainError("AUTO_SOURCE_DELETED", "岗位已不可用", 409)
    resume = _live_version(db, row.account_id, row.resume_version_id, "resume")
    job = _live_version(db, row.account_id, pool.job_document_version_id, "job")
    preferences = []
    for public_id in row.preference_version_ids:
        version = db.scalar(
            select(PreferenceVersion)
            .where(
                PreferenceVersion.public_id == public_id,
                PreferenceVersion.account_id == row.account_id,
                PreferenceVersion.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        profile = (
            db.get(Preference, version.preference_id, populate_existing=True) if version else None
        )
        if (
            version is None
            or profile is None
            or profile.account_id != row.account_id
            or profile.deleted_at is not None
            or profile.status != "active"
        ):
            raise DomainError("AUTO_SOURCE_DELETED", "岗位期望已不可用", 409)
        preferences.append(version)
    if not preferences:
        raise DomainError("AUTO_CONFIG_INVALID", "缺少冻结的岗位期望", 409)
    return pool, resume, job, preferences


def automatic_task_source(db: Session, task: Task) -> AutoMatchJob:
    row = db.scalar(
        select(AutoMatchJob)
        .where(
            AutoMatchJob.public_id == (task.input_data or {}).get("auto_match_job_id"),
            AutoMatchJob.account_id == task.account_id,
        )
        .execution_options(populate_existing=True)
    )
    account = db.get(Account, task.account_id, populate_existing=True)
    if (
        row is None
        or row.deleted_at is not None
        or row.state != "enqueued"
        or row.task_id != task.id
        or account is None
        or account.status != "active"
    ):
        raise NotFoundError("自动匹配来源已不可用")
    job_inputs(db, row)
    return row


def token_bounds(source_text: str) -> tuple[int, int]:
    # 第二次请求包含模型抽取后的 JSON，_json_text 限制为 80,000 字符。
    # UTF-8 字节数是保守 token 上界，另留出两次系统指令的空间；不以平均 token 猜预算。
    parse_size = len(
        json.dumps(
            {"document_type": "job_description", "source_text": source_text}, ensure_ascii=False
        ).encode("utf-8")
    )
    return parse_size + 320_000 + 8192, PARSE_OUTPUT_LIMIT + MATCH_OUTPUT_LIMIT


def _budget_block(db: Session, job: DocumentVersion) -> str | None:
    if settings.model_provider.lower() == "local":
        return None
    try:
        price = ensure_price_version(db, settings.model_provider.lower(), settings.model_name)
    except DomainError as exc:
        return exc.code
    document = db.get(Document, job.document_id)
    input_tokens, output_tokens = token_bounds(document.raw_text or "" if document else "")
    cost = token_upper_bound(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_usd_per_million=price.input_usd_per_million,
        output_usd_per_million=price.output_usd_per_million,
    )
    if cost is None:
        return "MODEL_PRICE_UNKNOWN"
    bucket = db.scalar(
        select(SiteBudgetBucket).where(SiteBudgetBucket.metric_date == metric_date())
    )
    remaining = (
        Decimal(bucket.budget_usd)
        - Decimal(bucket.reserved_usd)
        - Decimal(bucket.settled_usd)
        - Decimal(bucket.unknown_usd)
        if bucket
        else Decimal(settings.site_budget_usd)
    )
    if remaining < cost:
        return "BUDGET_LIMIT_REACHED"
    if bucket and bucket.concurrent_reserved >= bucket.concurrent_limit:
        return "BUDGET_CONCURRENCY_LIMIT"
    return None


def dispatch_account(
    db: Session, account_id: int, *, now: datetime | None = None, limit: int = 50
) -> int:
    """在一个短事务内登记待处理任务；从不在持锁时调用模型或 commit。"""
    from .api import _start_analysis

    _lock_account_write_lane(db, account_id)
    config = config_for(db, account_id)
    account = db.get(Account, account_id, populate_existing=True)
    if (
        config is None
        or account is None
        or account.status != "active"
        or account.registration_role != "seeker"
    ):
        return 0
    day = shanghai_date(now)
    today = (
        db.scalar(
            select(func.count(AutoMatchJob.id)).where(
                AutoMatchJob.account_id == account_id, AutoMatchJob.scheduled_date == day
            )
        )
        or 0
    )
    pending = (
        db.scalar(
            select(func.count(Task.id)).where(
                Task.account_id == account_id,
                Task.deleted_at.is_(None),
                Task.status.in_(["queued", "running", "retry_wait"]),
            )
        )
        or 0
    )
    balance = db.scalar(
        select(UsageBalance)
        .where(UsageBalance.account_id == account_id, UsageBalance.feature == "analysis")
        .with_for_update()
    )
    rows = db.scalars(
        select(AutoMatchJob)
        .where(
            AutoMatchJob.account_id == account_id,
            AutoMatchJob.deleted_at.is_(None),
            AutoMatchJob.state == "waiting",
        )
        .order_by(AutoMatchJob.created_at, AutoMatchJob.id)
        .limit(limit)
    ).all()
    count = 0
    for row in rows:
        pool = db.get(JobPoolItem, row.job_pool_item_id)
        if not config.enabled or not config.confirmed_at:
            row.blocked_code = "AUTO_PAUSED"
            continue
        try:
            # 没有有效快照的岗位只能在用户明确修正设置后取得输入，已有快照从不换版本。
            if row.resume_version_id is None:
                resume, preferences = selected_inputs(
                    db, account_id, config.resume_document_id, config.preference_ids
                )
                row.resume_version_id = resume.id
                row.preference_version_ids = [item.public_id for item in preferences]
                row.config_revision = config.revision
            pool, resume, job, preferences = job_inputs(db, row)
        except DomainError as exc:
            row.blocked_code = exc.code
            if exc.code == "AUTO_SOURCE_DELETED":
                row.state = "cancelled"
                row.deleted_at = utcnow()
            continue
        # 手动分析可以处理 waiting 岗位，但不能被自动队列再次领取。
        if db.scalar(select(Analysis.id).where(Analysis.job_pool_item_id == pool.id)):
            row.state, row.blocked_code = "cancelled", "AUTO_MANUAL_ANALYSIS"
            continue
        block = (
            "AUTO_DAILY_LIMIT"
            if today >= config.daily_limit
            else "USAGE_INSUFFICIENT"
            if balance is None or available_count(balance) < 1
            else "TASK_QUEUE_LIMIT_REACHED"
            if pending >= settings.account_pending_task_limit
            else _budget_block(db, job)
        )
        if block:
            row.blocked_code = block
            continue
        try:
            with request_context(row.request_id or ""):
                analysis, task, _ = _start_analysis(
                    db,
                    account,
                    context_type="seeker_pool",
                    resume_version=resume,
                    job_version=job,
                    preference=None,
                    preferences=preferences,
                    pool=pool,
                    key=f"auto-match:{row.public_id}",
                    enqueue_only=True,
                    automatic_job=row,
                )
            row.state = "enqueued"
            row.blocked_code = None
            row.analysis_id = analysis.id
            row.task_id = task.id
            row.scheduled_date = day
            pool.blocking_reasons = []
            today += 1
            pending += 1
            count += 1
            LOGGER.info(
                "automatic matching queued request_id=%s account_id=%s pool_id=%s automatic_job_id=%s task_id=%s rule_version=%s stage=enqueue",
                row.request_id or "unknown",
                account_id,
                pool.public_id,
                row.public_id,
                task.public_id,
                AUTOMATION_VERSION,
            )
        except Exception as exc:
            LOGGER.error(
                "automatic matching failed request_id=%s account_id=%s pool_id=%s automatic_job_id=%s rule_version=%s stage=enqueue error_type=%s error_code=%s",
                row.request_id or "unknown",
                account_id,
                pool.public_id,
                row.public_id,
                AUTOMATION_VERSION,
                type(exc).__name__,
                getattr(exc, "code", "AUTO_MATCH_FAILED"),
            )
            raise
    db.flush()
    return count


def automatic_view(db: Session, account_id: int, pool_id: int) -> dict[str, Any]:
    row = db.scalar(
        select(AutoMatchJob).where(
            AutoMatchJob.account_id == account_id, AutoMatchJob.job_pool_item_id == pool_id
        )
    )
    if row is None:
        pool = db.get(JobPoolItem, pool_id)
        prior = (
            db.scalar(
                select(AutoMatchJob).where(
                    AutoMatchJob.account_id == account_id,
                    AutoMatchJob.source_key
                    == payload_hash(
                        {"platform": pool.platform, "source_hash": pool.source_url_hash}
                    ),
                )
            )
            if pool and pool.source_type == "browser_capture"
            else None
        )
        if prior:
            return {
                "status": "duplicate",
                "message": "该岗位已经自动领取，不会重复扣次；更新后的内容可手动匹配",
            }
        return {"status": "not_enabled", "message": "已入池；开启自动处理后，新抓取岗位将自动匹配"}
    task = db.get(Task, row.task_id) if row.task_id else None
    state = task.status if task and row.deleted_at is None else row.state
    labels = {
        "queued": "已排队自动解析与匹配",
        "running": "正在自动解析与匹配",
        "succeeded": "分析已完成，可查看投递建议",
        "failed": "自动分析失败，请到任务中心重试",
        "retry_wait": "任务正在恢复",
        "cancelled": "自动处理已取消",
    }
    return {
        "id": row.public_id,
        "status": state,
        "blocked_code": row.blocked_code,
        "message": BLOCK_MESSAGES.get(row.blocked_code or "", labels.get(state, "已保留待处理")),
        "task_id": task.public_id if task else None,
        "request_id": row.request_id,
    }


def hide_jobs(
    db: Session,
    account_id: int,
    *,
    pool_ids: set[int] | None = None,
    version_ids: set[int] | None = None,
    analysis_ids: set[int] | None = None,
    preference_id: str | None = None,
) -> None:
    """删除事务内注销自动领取，连 waiting 引用也必须隐藏；从不释放已成功计次。"""
    from .api import _mark_tasks_cancelled

    rows = db.scalars(
        select(AutoMatchJob).where(
            AutoMatchJob.account_id == account_id, AutoMatchJob.deleted_at.is_(None)
        )
    ).all()
    affected = []
    profile = (
        db.scalar(
            select(Preference).where(
                Preference.public_id == preference_id, Preference.account_id == account_id
            )
        )
        if preference_id
        else None
    )
    deleted_preference_versions = (
        set(
            db.scalars(
                select(PreferenceVersion.public_id).where(
                    PreferenceVersion.preference_id == profile.id,
                    PreferenceVersion.account_id == account_id,
                )
            ).all()
        )
        if profile
        else set()
    )
    for row in rows:
        if (
            row.job_pool_item_id in (pool_ids or set())
            or row.resume_version_id in (version_ids or set())
            or row.analysis_id in (analysis_ids or set())
            or bool(deleted_preference_versions.intersection(row.preference_version_ids))
        ):
            affected.append(row)
    now = utcnow()
    _mark_tasks_cancelled(
        db,
        account_id,
        resource_ids={row.public_id for row in affected},
        task_ids={row.task_id for row in affected if row.task_id},
        reason="自动匹配来源已删除",
    )
    for row in affected:
        row.state = "cancelled"
        row.blocked_code = "AUTO_SOURCE_DELETED"
        row.deleted_at = now
        if preference_id and row.analysis_id:
            analysis = db.get(Analysis, row.analysis_id)
            if analysis and analysis.deleted_at is None:
                analysis.deleted_at = now
                analysis.result = None
            pool = db.get(JobPoolItem, row.job_pool_item_id)
            if pool and pool.deleted_at is None:
                pool.analysis_status = "awaiting_requirements"
                pool.blocking_reasons = ["自动处理的岗位期望已删除，请重新选择"]
    config = config_for(db, account_id)
    if config and (
        preference_id in config.preference_ids
        or (
            config.resume_document_id
            and any(
                db.get(DocumentVersion, version_id).document_id == config.resume_document_id
                for version_id in (version_ids or set())
                if db.get(DocumentVersion, version_id)
            )
        )
    ):
        config.enabled = False
        config.revision += 1
