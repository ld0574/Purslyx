"""模型价格、预算上界和并发槽的单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from server.app import budget
from server.app.budget import (
    ensure_price_version,
    metric_date,
    release_budget,
    reserve_budget,
    settle_budget,
)
from server.app.errors import DomainError
from server.app.models import BudgetReservation, ModelPriceVersion


class _Session:
    def __init__(self, scalar_values: list[Any] | None = None) -> None:
        self.scalar_values = list(scalar_values or [])
        self.added: list[Any] = []
        self.flush_count = 0

    def scalar(self, statement: Any) -> Any:
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value: Any) -> None:
        self.added.append(value)
        if isinstance(value, BudgetReservation) and value.id is None:
            value.id = 31

    def flush(self) -> None:
        self.flush_count += 1


def _price(rate: str = "10") -> SimpleNamespace:
    return SimpleNamespace(
        input_usd_per_million=Decimal(rate),
        cached_input_usd_per_million=Decimal("1"),
        output_usd_per_million=Decimal(rate),
    )


def _bucket(
    *,
    budget_usd: str = "10",
    reserved_usd: str = "0",
    settled_usd: str = "0",
    unknown_usd: str = "0",
    concurrent_reserved: int = 0,
    concurrent_limit: int = 2,
) -> SimpleNamespace:
    return SimpleNamespace(
        metric_date="2026-09-15",
        budget_usd=Decimal(budget_usd),
        reserved_usd=Decimal(reserved_usd),
        settled_usd=Decimal(settled_usd),
        unknown_usd=Decimal(unknown_usd),
        concurrent_reserved=concurrent_reserved,
        concurrent_limit=concurrent_limit,
    )


def test_metric_date_uses_shanghai_boundary() -> None:
    assert metric_date(datetime(2026, 9, 14, 15, 59, tzinfo=timezone.utc)) == "2026-09-14"
    assert metric_date(datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)) == "2026-09-15"


def test_local_price_is_explicit_zero_cost() -> None:
    db = _Session([None])
    price = ensure_price_version(
        db,  # type: ignore[arg-type]
        "local",
        "deterministic-v1",
        datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert isinstance(price, ModelPriceVersion)
    assert price.input_usd_per_million == Decimal("0")
    assert price.output_usd_per_million == Decimal("0")
    assert price.source_url == "local://deterministic-no-billing"


def test_unknown_remote_price_is_not_treated_as_zero() -> None:
    with pytest.raises(DomainError) as error:
        ensure_price_version(
            _Session([None]),  # type: ignore[arg-type]
            "remote-provider",
            "unknown-model",
            datetime(2026, 9, 15, tzinfo=timezone.utc),
        )
    assert error.value.code == "MODEL_PRICE_UNKNOWN"


def test_budget_reservation_and_duplicate_call_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = _bucket()
    monkeypatch.setattr(budget, "ensure_price_version", lambda *args, **kwargs: _price())
    monkeypatch.setattr(budget, "_bucket", lambda *args, **kwargs: bucket)
    db = _Session([None])

    reservation = reserve_budget(
        db,  # type: ignore[arg-type]
        account_id=7,
        task_id=20,
        provider="remote",
        model="model-v1",
        input_tokens=100_000,
        output_tokens=100_000,
        call_key="task-20:1:analysis",
        started_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert reservation.upper_bound_usd == Decimal("2.00000000")
    assert bucket.reserved_usd == Decimal("2.00000000")
    assert bucket.concurrent_reserved == 1

    duplicate_db = _Session([reservation])
    returned = reserve_budget(
        duplicate_db,  # type: ignore[arg-type]
        account_id=7,
        task_id=20,
        provider="remote",
        model="model-v1",
        input_tokens=999_999,
        output_tokens=999_999,
        call_key="task-20:1:analysis",
    )
    assert returned is reservation
    assert bucket.concurrent_reserved == 1
    assert duplicate_db.added == []


@pytest.mark.parametrize(
    ("bucket_value", "expected_code"),
    [
        (_bucket(budget_usd="1"), "BUDGET_LIMIT_REACHED"),
        (_bucket(concurrent_reserved=2, concurrent_limit=2), "BUDGET_CONCURRENCY_LIMIT"),
    ],
)
def test_budget_limits_block_before_reservation(
    monkeypatch: pytest.MonkeyPatch,
    bucket_value: SimpleNamespace,
    expected_code: str,
) -> None:
    monkeypatch.setattr(budget, "ensure_price_version", lambda *args, **kwargs: _price())
    monkeypatch.setattr(budget, "_bucket", lambda *args, **kwargs: bucket_value)
    db = _Session([None])
    with pytest.raises(DomainError) as error:
        reserve_budget(
            db,  # type: ignore[arg-type]
            account_id=7,
            task_id=20,
            provider="remote",
            model="model-v1",
            input_tokens=100_000,
            output_tokens=100_000,
            call_key="blocked-call",
        )
    assert error.value.code == expected_code
    assert db.added == []


def test_settle_known_cost_releases_slot_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket_value = _bucket(reserved_usd="2", concurrent_reserved=1)
    reservation = BudgetReservation(
        id=31,
        account_id=7,
        metric_date="2026-09-15",
        call_key="call-1",
        upper_bound_usd=Decimal("2"),
        status="reserved",
    )
    monkeypatch.setattr(budget, "_bucket", lambda *args, **kwargs: bucket_value)

    settle_budget(_Session([reservation]), 31, Decimal("1.25"))  # type: ignore[arg-type]
    assert reservation.status == "settled"
    assert reservation.actual_cost_usd == Decimal("1.25")
    assert bucket_value.reserved_usd == 0
    assert bucket_value.settled_usd == Decimal("1.25")
    assert bucket_value.concurrent_reserved == 0

    settle_budget(_Session([reservation]), 31, Decimal("9"))  # type: ignore[arg-type]
    assert bucket_value.settled_usd == Decimal("1.25")


def test_unknown_cost_is_held_and_not_counted_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket_value = _bucket(reserved_usd="2", concurrent_reserved=1)
    reservation = BudgetReservation(
        id=31,
        account_id=7,
        metric_date="2026-09-15",
        call_key="call-1",
        upper_bound_usd=Decimal("2"),
        status="reserved",
    )
    monkeypatch.setattr(budget, "_bucket", lambda *args, **kwargs: bucket_value)
    settle_budget(_Session([reservation]), 31, None)  # type: ignore[arg-type]
    assert reservation.status == "unknown"
    assert bucket_value.unknown_usd == Decimal("2")
    assert bucket_value.settled_usd == 0


def test_release_budget_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket_value = _bucket(reserved_usd="2", concurrent_reserved=1)
    reservation = BudgetReservation(
        id=31,
        account_id=7,
        metric_date="2026-09-15",
        call_key="call-1",
        upper_bound_usd=Decimal("2"),
        status="reserved",
    )
    monkeypatch.setattr(budget, "_bucket", lambda *args, **kwargs: bucket_value)
    release_budget(_Session([reservation]), 31)  # type: ignore[arg-type]
    assert reservation.status == "released"
    assert bucket_value.reserved_usd == 0
    assert bucket_value.concurrent_reserved == 0

    release_budget(_Session([reservation]), 31)  # type: ignore[arg-type]
    assert bucket_value.concurrent_reserved == 0
