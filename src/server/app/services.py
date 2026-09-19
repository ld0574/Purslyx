"""跨模块的轻量应用服务。

正式部署可以把这些函数拆成独立的 Repository 和 Worker；当前版本把事务边界集中在
这里，统一处理输入冻结、用量预留、任务执行和结果结算。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .budget import (
    actual_cost_from_price,
    ensure_price_version,
    release_budget,
    reserve_budget,
    settle_budget,
)
from .config import settings
from .errors import DomainError
from .model_provider import ModelResult
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


def encode_page_cursor(value: datetime, row_id: int) -> str:
    """生成不透明的倒序分页游标。

    游标只包含排序字段，不包含账号、过滤条件或业务正文；读取时仍会重新应用当前
    请求的账号和筛选条件。使用 base64url 只是编码，不把它当作安全凭据。
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    payload = {"v": 1, "at": value.astimezone(timezone.utc).isoformat(), "id": int(row_id)}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_page_cursor(value: str) -> tuple[datetime, int]:
    """解析并严格校验游标，拒绝任意 SQL 排序表达式或过大的输入。"""

    if not value or len(value) > 512:
        raise DomainError("PAGINATION_CURSOR_INVALID", "分页游标无效，请重新加载列表", 422, "refresh")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict):
            raise ValueError("cursor payload must be an object")
        timestamp = datetime.fromisoformat(str(payload["at"]))
        raw_row_id = payload["id"]
        if isinstance(raw_row_id, bool) or not isinstance(raw_row_id, int):
            raise ValueError("cursor id must be an integer")
        row_id = raw_row_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise DomainError("PAGINATION_CURSOR_INVALID", "分页游标无效，请重新加载列表", 422, "refresh") from exc
    if payload.get("v") != 1 or row_id < 1:
        raise DomainError("PAGINATION_CURSOR_INVALID", "分页游标无效，请重新加载列表", 422, "refresh")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc), row_id


def page_rows(
    db: Session,
    statement: Any,
    model: Any,
    *,
    cursor: str | None,
    limit: int,
    timestamp_field: str = "created_at",
) -> tuple[list[Any], dict[str, Any]]:
    """按 ``timestamp DESC, id DESC`` 读取一页并返回统一 page 元数据。"""

    if not 1 <= limit <= 100:
        raise DomainError("PAGINATION_LIMIT_INVALID", "limit 必须在 1 到 100 之间", 422)
    timestamp_column = getattr(model, timestamp_field)
    if cursor:
        timestamp, row_id = decode_page_cursor(cursor)
        statement = statement.where(
            or_(
                timestamp_column < timestamp,
                and_(timestamp_column == timestamp, model.id < row_id),
            )
        )
    statement = statement.order_by(timestamp_column.desc(), model.id.desc()).limit(limit + 1)
    rows = list(db.scalars(statement).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        next_cursor = encode_page_cursor(getattr(rows[-1], timestamp_field), rows[-1].id)
    return rows, {"next_cursor": next_cursor, "has_more": has_more, "limit": limit}


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


def _lock_account_write_lane(db: Session, account_id: int) -> None:
    """把同一账号的短写入判定串行化。

    PostgreSQL 唯一索引是最后一道防线；先锁账号行可以让并发重放在查询幂等事实前
    排队，从而让第二个请求返回原记录，而不是把唯一冲突暴露成服务端错误。
    """

    db.scalar(select(Account.id).where(Account.id == account_id).with_for_update())


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
    _lock_account_write_lane(db, account.id)
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
    """注册或邮箱验证事务内只发放一次适用的试用次数。"""

    grants: list[UsageGrant] = []
    for feature, count in TRIAL_COUNTS.get(account.registration_role, {}).items():
        grant, existed = grant_feature(
            db,
            account,
            feature,
            count,
            source_type="trial",
            reason="完成注册后的内测试用次数",
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
    _lock_account_write_lane(db, account.id)
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


def mark_outbox_published(db: Session, task_id: int) -> None:
    """同步本地 Worker 完成后确认事务 outbox，避免留下虚假的 pending 消息。"""

    task = db.get(Task, task_id)
    for event in db.scalars(
        select(TaskOutbox).where(TaskOutbox.task_id == task_id, TaskOutbox.status.in_(["pending", "claimed"]))
    ).all():
        # 旧执行代次在租约恢复后可能迟到；retry 事件必须留给新代次，不能被旧 Worker
        # 的失败清理逻辑一起标成 published。
        if task is not None and task.status in {"queued", "retry_wait"} and event.event_type == "task.retry":
            continue
        event.status = "published"
        event.claimed_by = None
        event.claimed_at = None
        event.published_at = utcnow()
        event.attempts += 1


def claim_outbox_batch(
    db: Session,
    *,
    owner: str,
    limit: int = 10,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> list[TaskOutbox]:
    """使用 PostgreSQL ``SKIP LOCKED`` 认领一批待执行 outbox 事件。

    ``claimed`` 事件在 Worker 崩溃后会重新变得可认领；认领只在当前事务内改变，
    调用方必须先提交，再执行模型调用或文件操作。
    """

    if not owner or len(owner) > 120:
        raise DomainError("WORKER_OWNER_INVALID", "Worker 标识无效", 422)
    if not 1 <= limit <= 100:
        raise DomainError("WORKER_BATCH_INVALID", "Worker 批量大小必须在 1 到 100 之间", 422)
    if lease_seconds < 1:
        raise DomainError("WORKER_LEASE_INVALID", "Worker 租约时长必须为正数", 422)
    current = now or utcnow()
    expired_before = current - timedelta(seconds=lease_seconds)
    statement = (
        select(TaskOutbox)
        .where(
            TaskOutbox.available_at <= current,
            or_(
                TaskOutbox.status == "pending",
                and_(
                    TaskOutbox.status == "claimed",
                    or_(TaskOutbox.claimed_at.is_(None), TaskOutbox.claimed_at <= expired_before),
                ),
            ),
        )
        .order_by(TaskOutbox.available_at.asc(), TaskOutbox.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    events = list(db.scalars(statement).all())
    for event in events:
        event.status = "claimed"
        event.claimed_by = owner
        event.claimed_at = current
        event.attempts += 1
    db.flush()
    return events


def _latest_attempt(db: Session, task_id: int) -> TaskAttempt | None:
    return db.scalar(
        select(TaskAttempt)
        .where(TaskAttempt.task_id == task_id)
        .order_by(TaskAttempt.execution_generation.desc(), TaskAttempt.id.desc())
    )


def _current_running_attempt(db: Session, task_id: int) -> TaskAttempt | None:
    return db.scalar(
        select(TaskAttempt)
        .where(TaskAttempt.task_id == task_id, TaskAttempt.status == "running")
        .order_by(TaskAttempt.execution_generation.desc(), TaskAttempt.id.desc())
    )


def assert_current_attempt(
    db: Session,
    task: Task,
    attempt: TaskAttempt,
    *,
    owner: str | None = None,
    now: datetime | None = None,
) -> None:
    """在写业务结果前确认执行代次仍是当前租约。"""

    current = _current_running_attempt(db, task.id)
    current_time = now or utcnow()
    if (
        task.status != "running"
        or current is None
        or current.id != attempt.id
        or current.execution_generation != attempt.execution_generation
        or (owner is not None and current.lease_owner != owner)
        or (current.lease_expires_at is not None and current.lease_expires_at <= current_time)
    ):
        raise DomainError("TASK_STALE_EXECUTION", "任务执行租约已失效，结果不会写回", 409, "retry")


def claim_task_execution(
    db: Session,
    task_id: int,
    *,
    owner: str,
    now: datetime | None = None,
    lease_seconds: int = 600,
) -> tuple[Task, TaskAttempt] | None:
    """锁定一个 queued/retry_wait 任务并创建或接管当前执行代次。"""

    if not owner or len(owner) > 120:
        raise DomainError("WORKER_OWNER_INVALID", "Worker 标识无效", 422)
    if lease_seconds < 1:
        raise DomainError("WORKER_LEASE_INVALID", "Worker 租约时长必须为正数", 422)
    current = now or utcnow()
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None or task.deleted_at is not None:
        return None
    if task.status not in {"queued", "retry_wait"}:
        return None
    if task.next_retry_at is not None and task.next_retry_at > current:
        return None

    queued_attempt = db.scalar(
        select(TaskAttempt)
        .where(TaskAttempt.task_id == task.id, TaskAttempt.status == "queued")
        .order_by(TaskAttempt.execution_generation.desc(), TaskAttempt.id.desc())
        .with_for_update()
    )
    if queued_attempt is None:
        latest = _latest_attempt(db, task.id)
        generation = max(task.retry_count + 1, (latest.execution_generation + 1) if latest else 1)
        attempt = TaskAttempt(task_id=task.id, execution_generation=generation, status="running")
        db.add(attempt)
    else:
        attempt = queued_attempt
        attempt.status = "running"
        if attempt.execution_generation <= task.retry_count:
            attempt.execution_generation = task.retry_count + 1
    attempt.lease_owner = owner
    attempt.lease_expires_at = current + timedelta(seconds=lease_seconds)
    attempt.error_code = None
    task.status = "running"
    task.current_step = "running"
    task.started_at = task.started_at or current
    task.next_retry_at = None
    task.failure = None
    db.flush()
    return task, attempt


def heartbeat_task(
    db: Session,
    task_id: int,
    attempt_id: int,
    *,
    owner: str,
    now: datetime | None = None,
    lease_seconds: int = 600,
) -> TaskAttempt:
    """延长当前任务租约；旧代次或错误 Worker 不得续租。"""

    current = now or utcnow()
    attempt = db.scalar(select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update())
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if attempt is None or task is None:
        raise DomainError("TASK_NOT_FOUND", "任务执行记录不存在", 404)
    assert_current_attempt(db, task, attempt, owner=owner, now=current)
    attempt.lease_expires_at = current + timedelta(seconds=lease_seconds)
    task.updated_at = current
    db.flush()
    return attempt


def recover_expired_leases(
    db: Session,
    *,
    limit: int = 50,
    now: datetime | None = None,
    retry_delay_seconds: int = 5,
) -> list[int]:
    """把过期执行代次转为可恢复任务，并为未知模型成本保留预算。"""

    from .budget import settle_budget
    from .models import BudgetReservation

    if not 1 <= limit <= 100:
        raise DomainError("WORKER_BATCH_INVALID", "Worker 批量大小必须在 1 到 100 之间", 422)
    current = now or utcnow()
    attempt_rows = list(
        db.scalars(
            select(TaskAttempt)
            .where(
                TaskAttempt.status == "running",
                TaskAttempt.lease_expires_at.is_not(None),
                TaskAttempt.lease_expires_at <= current,
            )
            .order_by(TaskAttempt.lease_expires_at.asc(), TaskAttempt.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    recovered: list[int] = []
    for expired_attempt in attempt_rows:
        task = db.scalar(select(Task).where(Task.id == expired_attempt.task_id).with_for_update())
        if task is None or task.deleted_at is not None:
            expired_attempt.status = "expired"
            expired_attempt.error_code = "TASK_SOURCE_DELETED"
            expired_attempt.finished_at = current
            continue
        latest_running = _current_running_attempt(db, task.id)
        if task.status != "running" or latest_running is None or latest_running.id != expired_attempt.id:
            expired_attempt.status = "expired"
            expired_attempt.error_code = "TASK_STALE_EXECUTION"
            expired_attempt.finished_at = current
            continue

        expired_attempt.status = "expired"
        expired_attempt.error_code = "TASK_LEASE_EXPIRED"
        expired_attempt.finished_at = current
        task.status = "retry_wait"
        task.current_step = "retry_wait"
        task.retry_count += 1
        task.next_retry_at = current + timedelta(seconds=max(0, retry_delay_seconds))
        task.failure = {
            "code": "TASK_LEASE_EXPIRED",
            "message": "Worker 租约过期，任务将自动恢复",
            "retryable": True,
        }
        # 模型调用是否已经发出无法从租约超时本身推断；未知预留不能释放成零成本。
        for reservation in db.scalars(
            select(BudgetReservation).where(
                BudgetReservation.task_id == task.id,
                BudgetReservation.status == "reserved",
            )
        ).all():
            settle_budget(db, reservation.id, None)
        db.add(TaskAttempt(task_id=task.id, execution_generation=task.retry_count + 1, status="queued"))
        retry_event = db.scalar(
            select(TaskOutbox).where(
                TaskOutbox.task_id == task.id,
                TaskOutbox.event_type == "task.retry",
                TaskOutbox.status.in_(["pending", "claimed"]),
            )
        )
        if retry_event is None:
            db.add(
                TaskOutbox(
                    task_id=task.id,
                    event_type="task.retry",
                    payload={"retry_count": task.retry_count, "reason": "lease_expired"},
                    available_at=task.next_retry_at,
                )
            )
        recovered.append(task.id)
    db.flush()
    return recovered


def mark_task_running(
    db: Session,
    task: Task,
    *,
    lease_owner: str = "local-postgres-worker",
    execution_generation: int | None = None,
) -> TaskAttempt:
    """记录本地 PostgreSQL Worker 的任务认领。"""

    task.status = "running"
    task.current_step = "running"
    task.started_at = utcnow()
    generation = execution_generation or task.retry_count + 1
    attempt = TaskAttempt(
        task_id=task.id,
        execution_generation=generation,
        lease_owner=lease_owner,
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

    if attempt is not None:
        assert_current_attempt(db, task, attempt)
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
    attempt = None
    if attempt_id:
        attempt = db.get(TaskAttempt, attempt_id)
        if attempt is None:
            return task
        current = _current_running_attempt(db, task.id)
        # 旧 Worker 的异常不能覆盖租约恢复后新代次的结果。
        if task.status != "running" or current is None or current.id != attempt.id:
            if attempt.status == "running":
                attempt.status = "stale"
                attempt.error_code = "TASK_STALE_EXECUTION"
                attempt.finished_at = utcnow()
            db.flush()
            return task
    task.status = "failed"
    task.current_step = "failed"
    task.failure = {
        "code": getattr(error, "code", "TASK_EXECUTION_FAILED"),
        "message": getattr(error, "message", "任务执行失败"),
        "retryable": True,
    }
    task.completed_at = utcnow()
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
    cost_usd: Decimal | float | None = None,
    error_code: str | None = None,
    duration_ms: int | None = None,
    budget_reservation_id: int | None = None,
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
    if budget_reservation_id is not None:
        from .models import BudgetReservation

        db.flush()
        reservation = db.get(BudgetReservation, budget_reservation_id)
        if reservation is None:
            raise DomainError("BUDGET_RESERVATION_NOT_FOUND", "模型预算预留不存在", 409)
        reservation.model_call_id = call.id
        settle_budget(db, budget_reservation_id, Decimal(str(cost_usd)) if cost_usd is not None else None)
    return call


def usage_view(
    db: Session,
    account: Account,
    feature: str | None = None,
    *,
    entries_cursor: str | None = None,
    entries_limit: int = 20,
) -> dict[str, Any]:
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
    entry_statement = select(UsageLedger).where(
        UsageLedger.account_id == account.id,
        *([UsageLedger.feature == feature] if feature else []),
    )
    entries, page = page_rows(
        db,
        entry_statement,
        UsageLedger,
        cursor=entries_cursor,
        limit=entries_limit,
        timestamp_field="created_at",
    )
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
                "source_id": (
                    db.get(Task, item.task_id).public_id
                    if item.task_id and db.get(Task, item.task_id) and db.get(Task, item.task_id).account_id == account.id
                    else None
                ),
                "reason": item.reason,
                "created_at": item.created_at.isoformat(),
            }
            for item in entries
        ],
        "page": page,
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
    work: Callable[[], dict[str, Any] | ModelResult],
    *,
    on_success: Callable[[dict[str, Any]], None] | None = None,
    feature: str | None = None,
    cost_feature: str | None = None,
    estimated_input_tokens: int = 12000,
    estimated_output_tokens: int = 4000,
    attempt: TaskAttempt | None = None,
    lease_owner: str = "local-postgres-worker",
    task_result: dict[str, Any] | None = None,
) -> None:
    """在没有 Celery 前置依赖时执行一个可追踪的本地任务。

    任务先提交 queued 状态，再执行本地确定性模型；因此即使请求中断，也能看到可恢复
    的任务记录。真实队列接入时可复用同一组状态迁移函数。
    """

    if settings.execution_mode == "worker":
        # HTTP 进程只负责创建任务、预留次数和提交 outbox；独立 Worker 会在提交后执行。
        # 不在这里预先认领，避免 API 进程和 Worker 同时写同一执行代次。
        db.commit()
        return

    budget_reservation = None
    provider_name = settings.model_provider.lower() or "local"
    model_name = settings.model_name
    billed_feature = cost_feature or feature
    try:
        attempt = attempt or mark_task_running(db, task, lease_owner=lease_owner)
        if billed_feature:
            budget_reservation = reserve_budget(
                db,
                account_id=task.account_id,
                task_id=task.id,
                provider=provider_name,
                model=model_name,
                input_tokens=0 if provider_name == "local" else estimated_input_tokens,
                output_tokens=0 if provider_name == "local" else estimated_output_tokens,
                call_key=f"{task.public_id}:{attempt.execution_generation}:{billed_feature}",
            )
        db.commit()
        raw_value = work()
        model_result = raw_value if isinstance(raw_value, ModelResult) else None
        value = model_result.value if model_result is not None else raw_value
        # commit 后刷新对象，避免旧事务状态覆盖其他 Worker 的字段。
        db.refresh(task)
        if on_success is not None:
            on_success(value)
        finish_task(db, task, reservation, task_result if task_result else value, attempt=attempt)
        if billed_feature:
            provider_name = model_result.provider if model_result is not None else provider_name
            model_name = model_result.model if model_result is not None else model_name
            input_tokens = model_result.input_tokens if model_result is not None else None
            output_tokens = model_result.output_tokens if model_result is not None else None
            actual_cost = model_result.cost_usd if model_result is not None else None
            price = ensure_price_version(db, provider_name, model_name)
            if actual_cost is None and provider_name == "local":
                actual_cost = Decimal("0")
            if actual_cost is None and input_tokens is not None and output_tokens is not None:
                actual_cost = actual_cost_from_price(
                    price,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            model_call(
                db,
                task.account_id,
                task.id,
                billed_feature,
                provider_name,
                model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=actual_cost,
                budget_reservation_id=budget_reservation.id if budget_reservation else None,
            )
        mark_outbox_published(db, task.id)
        db.commit()
    except Exception as exc:
        db.rollback()
        if budget_reservation is not None:
            try:
                if provider_name == "local":
                    release_budget(db, budget_reservation.id)
                else:
                    # 外部调用是否已发出无法可靠判断，预算必须转为未知持有，不能当作零成本释放。
                    settle_budget(db, budget_reservation.id, None)
            except Exception:
                db.rollback()
        fail_task(db, task.id, reservation.id if reservation else None, exc, attempt_id=attempt.id if attempt else None)
        if billed_feature:
            model_call(
                db,
                task.account_id,
                task.id,
                billed_feature,
                provider_name,
                model_name,
                status="failed",
                error_code=getattr(exc, "code", "TASK_EXECUTION_FAILED"),
                budget_reservation_id=None,
            )
        mark_outbox_published(db, task.id)
        db.commit()
        raise
