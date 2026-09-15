"""全站模型预算服务。

功能次数和美元成本是两套独立事实。这里的数据库操作只记录价格版本、上海自然日预算
桶和单次模型调用预留；模型正文不会进入预算表。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .errors import DomainError
from .pricing import calculate_cost, decimal_value, token_upper_bound

SHANGHAI = ZoneInfo("Asia/Shanghai")


def metric_date(value: datetime | None = None) -> str:
    """按上海自然日确定预算桶，不依赖 PostgreSQL 会话时区。"""

    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(SHANGHAI).date().isoformat()


def _price_for(db: Session, provider: str, model: str, now: datetime):
    from .models import ModelPriceVersion

    return db.scalar(
        select(ModelPriceVersion)
        .where(
            ModelPriceVersion.provider == provider,
            ModelPriceVersion.model == model,
            ModelPriceVersion.effective_from <= now,
            (ModelPriceVersion.effective_to.is_(None) | (ModelPriceVersion.effective_to > now)),
        )
        .order_by(ModelPriceVersion.effective_from.desc(), ModelPriceVersion.id.desc())
    )


def ensure_price_version(db: Session, provider: str, model: str, now: datetime | None = None):
    """读取生效价格；本地确定性模型明确记录为零成本，不把未知价格当零。"""

    current = now or datetime.now(timezone.utc)
    price = _price_for(db, provider, model, current)
    if price is not None:
        return price
    if provider == "local":
        from .models import ModelPriceVersion

        price = ModelPriceVersion(
            provider=provider,
            model=model,
            input_usd_per_million=Decimal("0"),
            cached_input_usd_per_million=Decimal("0"),
            output_usd_per_million=Decimal("0"),
            source_url="local://deterministic-no-billing",
            verified_on=current.astimezone(SHANGHAI).date().isoformat(),
            effective_from=current,
        )
        db.add(price)
        db.flush()
        return price
    raise DomainError("MODEL_PRICE_UNKNOWN", "当前模型没有可验证的价格版本，暂不能调用", 503, "retry")


def _bucket(db: Session, date_label: str):
    from .models import SiteBudgetBucket

    row = db.scalar(
        select(SiteBudgetBucket)
        .where(SiteBudgetBucket.metric_date == date_label)
        .with_for_update()
    )
    if row is None:
        row = SiteBudgetBucket(
            metric_date=date_label,
            budget_usd=decimal_value(settings.site_budget_usd, default=Decimal("30.00")) or Decimal("30.00"),
            concurrent_limit=settings.model_concurrency_limit,
        )
        db.add(row)
        db.flush()
    return row


def reserve_budget(
    db: Session,
    *,
    account_id: int,
    task_id: int | None,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    call_key: str,
    started_at: datetime | None = None,
):
    """预留成本上界和一个全站并发槽。"""

    from .models import BudgetReservation

    existing = db.scalar(select(BudgetReservation).where(BudgetReservation.call_key == call_key))
    if existing is not None:
        return existing
    now = started_at or datetime.now(timezone.utc)
    price = ensure_price_version(db, provider, model, now)
    upper_bound = token_upper_bound(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_usd_per_million=price.input_usd_per_million,
        output_usd_per_million=price.output_usd_per_million,
    )
    if upper_bound is None:
        raise DomainError("MODEL_PRICE_UNKNOWN", "当前模型价格或 token 上界不可解释", 503, "retry")
    bucket = _bucket(db, metric_date(now))
    available = (
        Decimal(bucket.budget_usd)
        - Decimal(bucket.reserved_usd)
        - Decimal(bucket.settled_usd)
        - Decimal(bucket.unknown_usd)
    )
    if available < upper_bound:
        raise DomainError("BUDGET_LIMIT_REACHED", "全站模型预算暂不足，请稍后重试", 429, "retry")
    if bucket.concurrent_reserved >= bucket.concurrent_limit:
        raise DomainError("BUDGET_CONCURRENCY_LIMIT", "全站模型并发已满，请稍后重试", 429, "retry")
    bucket.reserved_usd = Decimal(bucket.reserved_usd) + upper_bound
    bucket.concurrent_reserved += 1
    reservation = BudgetReservation(
        account_id=account_id,
        task_id=task_id,
        metric_date=bucket.metric_date,
        call_key=call_key,
        upper_bound_usd=upper_bound,
    )
    db.add(reservation)
    db.flush()
    return reservation


def settle_budget(db: Session, reservation_id: int, actual_cost: Decimal | None):
    """把预留结算为已知成本，或转成未知成本持有。"""

    from .models import BudgetReservation

    reservation = db.scalar(select(BudgetReservation).where(BudgetReservation.id == reservation_id).with_for_update())
    if reservation is None:
        raise DomainError("BUDGET_RESERVATION_NOT_FOUND", "模型预算预留不存在", 409)
    if reservation.status in {"settled", "released", "unknown"}:
        return reservation
    bucket = _bucket(db, reservation.metric_date)
    upper = Decimal(reservation.upper_bound_usd)
    bucket.reserved_usd = max(Decimal("0"), Decimal(bucket.reserved_usd) - upper)
    bucket.concurrent_reserved = max(0, bucket.concurrent_reserved - 1)
    if actual_cost is None:
        reservation.status = "unknown"
        bucket.unknown_usd = Decimal(bucket.unknown_usd) + upper
    else:
        if actual_cost < 0 or not actual_cost.is_finite():
            raise DomainError("MODEL_COST_INVALID", "模型成本不可解释", 500)
        reservation.status = "settled"
        reservation.actual_cost_usd = actual_cost
        bucket.settled_usd = Decimal(bucket.settled_usd) + actual_cost
    db.flush()
    return reservation


def release_budget(db: Session, reservation_id: int):
    """确定模型没有执行时释放上界和并发槽。"""

    from .models import BudgetReservation

    reservation = db.scalar(select(BudgetReservation).where(BudgetReservation.id == reservation_id).with_for_update())
    if reservation is None:
        raise DomainError("BUDGET_RESERVATION_NOT_FOUND", "模型预算预留不存在", 409)
    if reservation.status != "reserved":
        return reservation
    bucket = _bucket(db, reservation.metric_date)
    bucket.reserved_usd = max(Decimal("0"), Decimal(bucket.reserved_usd) - Decimal(reservation.upper_bound_usd))
    bucket.concurrent_reserved = max(0, bucket.concurrent_reserved - 1)
    reservation.status = "released"
    db.flush()
    return reservation


def actual_cost_from_price(price, *, input_tokens: int | None, output_tokens: int | None, cached_input_tokens: int | None = None) -> Decimal | None:
    """从已选择的价格版本计算供应商返回 token 的实际成本。"""

    return calculate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        input_usd_per_million=price.input_usd_per_million,
        cached_input_usd_per_million=price.cached_input_usd_per_million,
        output_usd_per_million=price.output_usd_per_million,
    )
