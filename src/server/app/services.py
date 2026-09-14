"""跨模块的轻量应用服务。

正式部署可以把这些函数拆成独立的 Repository 和 Worker；当前版本把事务边界集中在
这里，方便明天用一条可运行的代码路径演示 SDD 的输入冻结、用量预留和结果结算。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import DomainError
from .models import (
    Account,
    ModelCall,
    Task,
    TaskAttempt,
    TaskInputRef,
    TaskOutbox,
    UsageBalance,
    UsageGrant,
    UsageLedger,
    UsageReservation,
)
from .security import require_seeker


FEATURES_BY_ROLE = {
    "seeker": {"analysis", "rewrite", "interview"},
    "recruiter": {"analysis"},
}
TRIAL_COUNTS = {"seeker": {"analysis": 20, "rewrite": 20, "interview": 5}, "recruiter": {"analysis": 20}}


def utcnow() -> datetime:
    """返回带时区的 UTC 时间。"""

    return datetime.now(timezone.utc)


def shanghai_date(value: datetime | None = None) -> str:
    """把时间转换成统计约定的上海自然日。"""

    from zoneinfo import ZoneInfo

    return (value or utcnow()).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def payload_hash(value: Any) -> str:
    """为幂等冲突比较生成稳定摘要，不记录正文到日志。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def feature_allowed(account: Account, feature: str) -> bool:
    return feature in FEATURES_BY_ROLE.get(account.registration_role, set())


def check_feature_allowed(account: Account, feature: str) -> None:
    if not feature_allowed(account, feature):
        raise DomainError("USAGE_FEATURE_NOT_ALLOWED", "当前注册身份不适用该功能", 403)


def available_count(balance: UsageBalance) -> int:
    """余额事实始终由发放、预留和结算三列计算。"""

    return balance.granted - balance.reserved - balance.consumed


def get_or_create_balance(db: Session, account_id: int, feature: str, *, lock: bool = False) -> UsageBalance:
    statement = select(UsageBalance).where(
        UsageBalance.account_id == account_id,
        UsageBalance.feature == feature,
    )
    if lock:
        statement = statement.with_for_update()
    balance = db.scalar(statement)
    if balance is None:
        balance = UsageBalance(account_id=account_id, feature=feature)
        db.add(balance)
        db.flush()
    return balance


def grant_feature(
    db: Session,
    account: Account,
    feature: str,
    count: int,
    *,
    source_type: str,
    reason: str,
    operator_account_id: int | None = None,
    batch_key: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[UsageGrant, bool]:
    """追加一次发放事实；返回 (发放记录, 是否为已存在记录)。"""

    check_feature_allowed(account, feature)
    if count <= 0:
        raise DomainError("USAGE_GRANT_INVALID", "发放次数必须是正整数", 422)
    if not reason.strip():
        raise DomainError("USAGE_GRANT_INVALID", "发放原因不能为空", 422)
    existing = None
    if batch_key:
        existing = db.scalar(
            select(UsageGrant).where(
                UsageGrant.account_id == account.id,
                UsageGrant.feature == feature,
                UsageGrant.batch_key == batch_key,
            )
        )
    if idempotency_key:
        existing_by_key = db.scalar(
            select(UsageGrant).where(
                UsageGrant.account_id == account.id,
                UsageGrant.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            existing = existing_by_key
        elif existing_by_key is not None and existing_by_key.id != existing.id:
            raise DomainError("USAGE_IDEMPOTENCY_CONFLICT", "幂等键已经用于另一笔发放", 409)
    if existing is not None:
        if existing.count != count or existing.reason != reason or existing.feature != feature:
            raise DomainError("USAGE_IDEMPOTENCY_CONFLICT", "幂等键对应的发放参数不同", 409)
        return existing, True

    # 操作者和目标账号已由调用方校验；这里锁余额，保证可用值不会被并发读旧。
    balance = get_or_create_balance(db, account.id, feature, lock=True)
    before = available_count(balance)
    balance.granted += count
    after = available_count(balance)
    grant = UsageGrant(
        account_id=account.id,
        operator_account_id=operator_account_id,
        feature=feature,
        count=count,
        source_type=source_type,
        batch_key=batch_key,
        idempotency_key=idempotency_key,
        reason=reason.strip(),
        before_available=before,
        after_available=after,
    )
    db.add(grant)
    db.add(
        UsageLedger(
            account_id=account.id,
            feature=feature,
            event_type="grant",
            amount=count,
            idempotency_key=idempotency_key or batch_key,
            reason=reason.strip(),
        )
    )
    db.flush()
    return grant, False


def grant_trial_if_needed(db: Session, account: Account) -> list[UsageGrant]:
    """邮箱验证事务内只发放一次适用的试用次数。"""

    grants: list[UsageGrant] = []
    for feature, count in TRIAL_COUNTS.get(account.registration_role, {}).items():
        grant, existed = grant_feature(
            db,
            account,
            feature,
            count,
            source_type="trial",
            reason="完成邮箱验证后的内测试用次数",
            batch_key="internal-beta-v1",
        )
        if not existed:
            grants.append(grant)
    return grants


def reserve_feature(
    db: Session,
    account: Account,
    task: Task,
    feature: str,
    *,
    count: int = 1,
    idempotency_key: str | None = None,
) -> UsageReservation:
    """在任务执行前原子预留功能次数。"""

    check_feature_allowed(account, feature)
    existing = db.scalar(
        select(UsageReservation).where(
            UsageReservation.task_id == task.id,
            UsageReservation.feature == feature,
        )
    )
    if existing is not None:
        if existing.count != count:
            raise DomainError("USAGE_IDEMPOTENCY_CONFLICT", "同一任务的预留数量不能变化", 409)
        if existing.status == "reserved":
            return existing
        if existing.status == "settled":
            return existing
        # 手动重试沿用同一个业务任务；释放过的预留必须重新占用余额，
        # 不能因为唯一约束而把重试误当成免费调用。
        balance = get_or_create_balance(db, account.id, feature, lock=True)
        if available_count(balance) < count:
            raise DomainError("USAGE_INSUFFICIENT", "可用次数不足，请先补充次数后再执行", 429, "grant_usage")
        balance.reserved += count
        existing.status = "reserved"
        existing.released_at = None
        existing.settled_at = None
        existing.idempotency_key = idempotency_key or existing.idempotency_key
        task.usage_feature = feature
        task.usage_reserved = count
        db.add(
            UsageLedger(
                account_id=account.id,
                feature=feature,
                event_type="reserve",
                amount=count,
                task_id=task.id,
                idempotency_key=idempotency_key,
                reason="任务重试前重新预留",
            )
        )
        db.flush()
        return existing
    balance = get_or_create_balance(db, account.id, feature, lock=True)
    if available_count(balance) < count:
        raise DomainError("USAGE_INSUFFICIENT", "可用次数不足，请先补充次数后再执行", 429, "grant_usage")
    balance.reserved += count
    reservation = UsageReservation(
        account_id=account.id,
        task_id=task.id,
        feature=feature,
        count=count,
        idempotency_key=idempotency_key,
    )
    task.usage_feature = feature
    task.usage_reserved = count
    db.add(reservation)
    db.add(
        UsageLedger(
            account_id=account.id,
            feature=feature,
            event_type="reserve",
            amount=count,
            task_id=task.id,
            idempotency_key=idempotency_key,
            reason="任务开始前预留",
        )
    )
    db.flush()
    return reservation


def settle_feature(db: Session, reservation_id: int) -> UsageReservation:
    """成功结果可读后结算；重复调用保持幂等。"""

    reservation = db.scalar(select(UsageReservation).where(UsageReservation.id == reservation_id).with_for_update())
    if reservation is None:
        raise DomainError("USAGE_RESERVATION_NOT_FOUND", "使用次数预留不存在", 409)
    if reservation.status == "settled":
        return reservation
    if reservation.status == "released":
        raise DomainError("USAGE_RESERVATION_FINALIZED", "已释放的使用次数不能再次结算", 409)
    balance = get_or_create_balance(db, reservation.account_id, reservation.feature, lock=True)
    balance.reserved -= reservation.count
    balance.consumed += reservation.count
    reservation.status = "settled"
    reservation.settled_at = utcnow()
    db.add(
        UsageLedger(
            account_id=reservation.account_id,
            feature=reservation.feature,
            event_type="settle",
            amount=reservation.count,
            task_id=reservation.task_id,
            idempotency_key=f"settle:{reservation.public_id}",
            reason="任务成功并交付结果",
        )
    )
    db.flush()
    return reservation


def release_feature(db: Session, reservation_id: int, reason: str = "任务未交付结果") -> UsageReservation:
    """失败或来源失效时释放仍处于 reserved 的次数。"""

    reservation = db.scalar(select(UsageReservation).where(UsageReservation.id == reservation_id).with_for_update())
    if reservation is None:
        raise DomainError("USAGE_RESERVATION_NOT_FOUND", "使用次数预留不存在", 409)
    if reservation.status == "released":
        return reservation
    if reservation.status == "settled":
        return reservation
    balance = get_or_create_balance(db, reservation.account_id, reservation.feature, lock=True)
    balance.reserved -= reservation.count
    reservation.status = "released"
    reservation.released_at = utcnow()
    db.add(
        UsageLedger(
            account_id=reservation.account_id,
            feature=reservation.feature,
            event_type="release",
            amount=reservation.count,
            task_id=reservation.task_id,
            idempotency_key=f"release:{reservation.public_id}",
            reason=reason,
        )
    )
    db.flush()
    return reservation


def create_task(
    db: Session,
    account: Account,
    task_type: str,
    input_data: dict[str, Any],
    *,
    feature: str | None = None,
    idempotency_key: str | None = None,
    input_refs: list[tuple[str, str, int | None, str | None]] | None = None,
) -> tuple[Task, UsageReservation | None, bool]:
    """创建任务、冻结输入、写 outbox，并在同一事务内预留次数。"""

    request_digest = payload_hash(input_data)
    existing = None
    if idempotency_key:
        existing = db.scalar(
            select(Task).where(
                Task.account_id == account.id,
                Task.task_type == task_type,
                Task.idempotency_key == idempotency_key,
                Task.deleted_at.is_(None),
            )
        )
    if existing is not None:
        stored_digest = existing.request_hash or payload_hash(existing.input_data)
        if stored_digest != request_digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的任务输入不同", 409)
        reservation = None
        if feature:
            reservation = db.scalar(
                select(UsageReservation).where(
                    UsageReservation.task_id == existing.id,
                    UsageReservation.feature == feature,
                )
            )
        return existing, reservation, True

    frozen_refs = [
        {
            "resource_type": resource_type,
            "resource_id": resource_public_id,
            "version_no": version_no,
        }
        for resource_type, resource_public_id, version_no, _ in input_refs or []
    ]
    stored_input = dict(input_data)
    # 任务详情只需要版本引用，不把正文复制进任务日志；这是断线恢复和审计的最小快照。
    stored_input.setdefault("input_versions", frozen_refs)
    task = Task(
        account_id=account.id,
        task_type=task_type,
        status="queued",
        current_step="queued",
        progress={"completed": 0, "total": 1},
        input_data=stored_input,
        idempotency_key=idempotency_key,
        request_hash=request_digest,
    )
    db.add(task)
    db.flush()
    reservation = None
    if feature:
        reservation = reserve_feature(db, account, task, feature, idempotency_key=idempotency_key)
    for resource_type, resource_public_id, version_no, snapshot_hash in input_refs or []:
        db.add(
            TaskInputRef(
                task_id=task.id,
                account_id=account.id,
                resource_type=resource_type,
                resource_public_id=resource_public_id,
                version_no=version_no,
                snapshot_hash=snapshot_hash,
            )
        )
    db.add(TaskOutbox(task_id=task.id, event_type="task.created", payload={"task_type": task_type}))
    db.flush()
    return task, reservation, False


def mark_task_running(db: Session, task: Task) -> TaskAttempt:
    """同步演示 Worker 的认领记录。"""

    task.status = "running"
    task.current_step = "running"
    task.started_at = utcnow()
    attempt = TaskAttempt(
        task_id=task.id,
        execution_generation=task.retry_count + 1,
        lease_owner="local-demo-worker",
        lease_expires_at=utcnow() + timedelta(minutes=10),
        status="running",
    )
    db.add(attempt)
    db.flush()
    return attempt


def finish_task(
    db: Session,
    task: Task,
    reservation: UsageReservation | None,
    result: dict[str, Any],
    *,
    attempt: TaskAttempt | None = None,
) -> None:
    """业务结果写入后再结算功能次数。"""

    task.status = "succeeded"
    task.current_step = "completed"
    task.progress = {"completed": 1, "total": 1}
    task.result = result
    task.completed_at = utcnow()
    if attempt is not None:
        attempt.status = "succeeded"
        attempt.finished_at = utcnow()
    if reservation is not None:
        settle_feature(db, reservation.id)


def fail_task(
    db: Session,
    task_id: int,
    reservation_id: int | None,
    error: DomainError | Exception,
    *,
    attempt_id: int | None = None,
) -> Task:
    """失败状态与次数释放分开提交，保证响应异常时仍可恢复。"""

    task = db.get(Task, task_id)
    if task is None:
        raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
    task.status = "failed"
    task.current_step = "failed"
    task.failure = {
        "code": getattr(error, "code", "TASK_EXECUTION_FAILED"),
        "message": getattr(error, "message", "任务执行失败"),
        "retryable": True,
    }
    task.completed_at = utcnow()
    if attempt_id:
        attempt = db.get(TaskAttempt, attempt_id)
        if attempt is not None:
            attempt.status = "failed"
            attempt.error_code = task.failure["code"]
            attempt.finished_at = utcnow()
    if reservation_id is not None:
        release_feature(db, reservation_id, "任务失败，结果未交付")
    db.flush()
    return task


def model_call(
    db: Session,
    account_id: int,
    task_id: int | None,
    feature: str,
    provider: str,
    model: str,
    *,
    status: str = "succeeded",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    error_code: str | None = None,
    duration_ms: int | None = None,
) -> ModelCall:
    """记录模型调用元数据，不保存简历、JD 或回答正文。"""

    call = ModelCall(
        account_id=account_id,
        task_id=task_id,
        feature=feature,
        provider=provider,
        model=model,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        error_code=error_code,
        duration_ms=duration_ms,
    )
    db.add(call)
    return call


def usage_view(db: Session, account: Account, feature: str | None = None) -> dict[str, Any]:
    features = sorted(FEATURES_BY_ROLE.get(account.registration_role, set()))
    if feature:
        check_feature_allowed(account, feature)
        features = [feature]
    balances = []
    for item in features:
        balance = get_or_create_balance(db, account.id, item)
        balances.append(
            {
                "feature": item,
                "unit": "sessions" if item == "interview" else "times",
                "granted_total": balance.granted,
                "reserved_total": balance.reserved,
                "settled_total": balance.consumed,
                "available": available_count(balance),
                "updated_at": balance.updated_at.isoformat(),
            }
        )
    entries = db.scalars(
        select(UsageLedger)
        .where(UsageLedger.account_id == account.id, *([UsageLedger.feature == feature] if feature else []))
        .order_by(UsageLedger.created_at.desc())
        .limit(100)
    ).all()
    return {
        "registration_role": account.registration_role,
        "balances": balances,
        "entries": [
            {
                "id": item.public_id,
                "feature": item.feature,
                "entry_type": item.event_type,
                "count": item.amount,
                "source_type": "task" if item.task_id else ("trial" if item.event_type == "grant" else "admin_grant"),
                "source_id": None,
                "reason": item.reason,
                "created_at": item.created_at.isoformat(),
            }
            for item in entries
        ],
        "page": {"next_cursor": None, "has_more": False},
    }


def task_view(task: Task) -> dict[str, Any]:
    """统一任务摘要，避免把内部自增 ID 返回给客户端。"""

    progress = dict(task.progress or {})
    if "completed_steps" not in progress and "completed" in progress:
        progress["completed_steps"] = progress.get("completed", 0)
    if "total_steps" not in progress and "total" in progress:
        progress["total_steps"] = progress.get("total", 0)
    if "display_percent" not in progress:
        total = progress.get("total_steps") or 0
        completed = progress.get("completed_steps") or 0
        progress["display_percent"] = round(completed / total * 100) if total else None
    retryable = bool((task.failure or {}).get("retryable")) and task.status in {"failed", "retry_wait", "needs_input"}
    return {
        "id": task.public_id,
        "task_type": task.task_type,
        "status": task.status,
        "current_step": task.current_step,
        "progress": progress,
        "input_versions": task.input_data.get("input_versions", []) if isinstance(task.input_data, dict) else [],
        "usage_reservation": (
            {"feature": task.usage_feature, "count": task.usage_reserved}
            if task.usage_feature
            else None
        ),
        "result": task.result,
        "failure": task.failure,
        "required_actions": task.required_actions or [],
        "retryable": retryable,
        "poll_after_ms": 2000 if task.status in {"queued", "running", "retry_wait"} else None,
        "retry_count": task.retry_count,
        "next_retry_at": task.next_retry_at.isoformat() if task.next_retry_at else None,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def run_local_task(
    db: Session,
    task: Task,
    reservation: UsageReservation | None,
    work: Callable[[], dict[str, Any]],
    *,
    on_success: Callable[[dict[str, Any]], None] | None = None,
    feature: str | None = None,
) -> None:
    """在没有 Celery 前置依赖时执行一个可追踪的本地任务。

    任务先提交 queued 状态，再执行本地确定性模型；因此即使请求中断，也能看到可恢复
    的任务记录。真实队列接入时可复用同一组状态迁移函数。
    """

    attempt: TaskAttempt | None = None
    try:
        attempt = mark_task_running(db, task)
        db.commit()
        value = work()
        # commit 后刷新对象，避免旧事务状态覆盖其他 Worker 的字段。
        db.refresh(task)
        if on_success is not None:
            on_success(value)
        finish_task(db, task, reservation, value, attempt=attempt)
        if feature:
            model_call(db, task.account_id, task.id, feature, "local", "deterministic-v1")
        db.commit()
    except Exception as exc:
        db.rollback()
        fail_task(db, task.id, reservation.id if reservation else None, exc, attempt_id=attempt.id if attempt else None)
        if feature:
            model_call(
                db,
                task.account_id,
                task.id,
                feature,
                "local",
                "deterministic-v1",
                status="failed",
                error_code=getattr(exc, "code", "TASK_EXECUTION_FAILED"),
            )
        db.commit()
        raise
