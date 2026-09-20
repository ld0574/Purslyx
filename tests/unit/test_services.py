"""任务幂等、用量预留与结算服务的单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from server.app import services
from server.app.errors import DomainError
from server.app.models import (
    Account,
    Task,
    TaskAttempt,
    TaskInputRef,
    TaskOutbox,
    UsageBalance,
    UsageGrant,
    UsageLedger,
    UsageReservation,
)
from server.app.services import (
    available_count,
    check_feature_allowed,
    create_task,
    decode_page_cursor,
    decode_score_page_cursor,
    encode_page_cursor,
    encode_score_page_cursor,
    grant_feature,
    payload_hash,
    release_feature,
    reserve_feature,
    run_local_task,
    settle_feature,
    task_view,
)


class _Session:
    """按队列返回 scalar 结果，并记录当前事务新增对象。"""

    def __init__(self, scalar_values: list[Any] | None = None) -> None:
        self.scalar_values = list(scalar_values or [])
        self.added: list[Any] = []
        self.flush_count = 0

    def scalar(self, statement: Any) -> Any:
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value: Any) -> None:
        self.added.append(value)
        if isinstance(value, Task) and value.id is None:
            value.id = 100

    def flush(self) -> None:
        self.flush_count += 1


def _account(role: str = "seeker") -> Account:
    return Account(id=7, public_id="account-7", email="user@example.test", registration_role=role)


def _balance(*, granted: int = 5, reserved: int = 0, consumed: int = 0) -> UsageBalance:
    return UsageBalance(
        id=1,
        account_id=7,
        feature="analysis",
        granted=granted,
        reserved=reserved,
        consumed=consumed,
        updated_at=datetime.now(timezone.utc),
    )


def test_payload_hash_and_cursor_are_stable() -> None:
    assert payload_hash({"b": 2, "a": 1}) == payload_hash({"a": 1, "b": 2})
    timestamp = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)
    cursor = encode_page_cursor(timestamp, 42)
    assert decode_page_cursor(cursor) == (timestamp, 42)
    score_cursor = encode_score_page_cursor(88.5, 42)
    assert decode_score_page_cursor(score_cursor) == (88.5, 42)
    empty_score_cursor = encode_score_page_cursor(None, 43)
    assert decode_score_page_cursor(empty_score_cursor) == (None, 43)


@pytest.mark.parametrize("cursor", ["", "not-base64", "e30", "a" * 513])
def test_invalid_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(DomainError) as error:
        decode_page_cursor(cursor)
    assert error.value.code == "PAGINATION_CURSOR_INVALID"


def test_role_features_and_available_balance() -> None:
    assert available_count(_balance(granted=20, reserved=3, consumed=4)) == 13
    check_feature_allowed(_account("seeker"), "interview")
    check_feature_allowed(_account("recruiter"), "analysis")
    with pytest.raises(DomainError) as error:
        check_feature_allowed(_account("recruiter"), "rewrite")
    assert error.value.code == "USAGE_FEATURE_NOT_ALLOWED"


def test_grant_feature_updates_balance_and_writes_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    balance = _balance(granted=5)
    monkeypatch.setattr(services, "get_or_create_balance", lambda *args, **kwargs: balance)
    db = _Session()

    grant, existed = grant_feature(
        db,  # type: ignore[arg-type]
        _account(),
        "analysis",
        3,
        source_type="admin",
        reason="补充回归测试次数",
        idempotency_key="grant-1",
    )

    assert existed is False
    assert balance.granted == 8
    assert grant.before_available == 5
    assert grant.after_available == 8
    assert any(isinstance(value, UsageLedger) and value.event_type == "grant" for value in db.added)


def test_grant_feature_reuses_same_idempotency_record() -> None:
    existing = UsageGrant(
        id=9,
        account_id=7,
        feature="analysis",
        count=3,
        source_type="admin",
        reason="补充次数",
        before_available=5,
        after_available=8,
        idempotency_key="grant-1",
    )
    db = _Session([7, existing])
    result, existed = grant_feature(
        db,  # type: ignore[arg-type]
        _account(),
        "analysis",
        3,
        source_type="admin",
        reason="补充次数",
        idempotency_key="grant-1",
    )
    assert result is existing
    assert existed is True
    assert db.added == []

    conflict_db = _Session([7, existing])
    with pytest.raises(DomainError) as error:
        grant_feature(
            conflict_db,  # type: ignore[arg-type]
            _account(),
            "analysis",
            4,
            source_type="admin",
            reason="补充次数",
            idempotency_key="grant-1",
        )
    assert error.value.code == "USAGE_IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(("count", "reason"), [(0, "原因"), (-1, "原因"), (1, "  ")])
def test_invalid_grant_is_rejected(count: int, reason: str) -> None:
    with pytest.raises(DomainError) as error:
        grant_feature(
            _Session(),  # type: ignore[arg-type]
            _account(),
            "analysis",
            count,
            source_type="admin",
            reason=reason,
        )
    assert error.value.code == "USAGE_GRANT_INVALID"


def test_reserve_then_settle_consumes_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    balance = _balance(granted=2)
    monkeypatch.setattr(services, "get_or_create_balance", lambda *args, **kwargs: balance)
    task = Task(id=20, account_id=7, task_type="analysis", input_data={})
    reserve_db = _Session([None])

    reservation = reserve_feature(
        reserve_db,  # type: ignore[arg-type]
        _account(),
        task,
        "analysis",
        idempotency_key="analysis-1",
    )
    reservation.id = 30
    reservation.public_id = "reservation-30"
    reservation.status = "reserved"
    assert balance.reserved == 1
    assert balance.consumed == 0
    assert task.usage_feature == "analysis"

    settle_db = _Session([reservation])
    settle_feature(settle_db, reservation.id)  # type: ignore[arg-type]
    assert balance.reserved == 0
    assert balance.consumed == 1
    assert reservation.status == "settled"
    assert sum(isinstance(value, UsageLedger) for value in settle_db.added) == 1

    duplicate_db = _Session([reservation])
    settle_feature(duplicate_db, reservation.id)  # type: ignore[arg-type]
    assert balance.consumed == 1
    assert duplicate_db.added == []


def test_release_restores_availability_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    balance = _balance(granted=2, reserved=1)
    reservation = UsageReservation(
        id=30,
        public_id="reservation-30",
        account_id=7,
        task_id=20,
        feature="analysis",
        count=1,
        status="reserved",
    )
    monkeypatch.setattr(services, "get_or_create_balance", lambda *args, **kwargs: balance)

    release_db = _Session([reservation])
    release_feature(release_db, reservation.id, "模型调用失败")  # type: ignore[arg-type]
    assert balance.reserved == 0
    assert balance.consumed == 0
    assert available_count(balance) == 2
    assert reservation.status == "released"

    duplicate_db = _Session([reservation])
    release_feature(duplicate_db, reservation.id)  # type: ignore[arg-type]
    assert balance.reserved == 0
    assert duplicate_db.added == []


def test_reserve_rejects_insufficient_balance_before_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        services,
        "get_or_create_balance",
        lambda *args, **kwargs: _balance(granted=1, reserved=1),
    )
    with pytest.raises(DomainError) as error:
        reserve_feature(
            _Session([None]),  # type: ignore[arg-type]
            _account(),
            Task(id=20, account_id=7, task_type="analysis", input_data={}),
            "analysis",
        )
    assert error.value.code == "USAGE_INSUFFICIENT"
    assert error.value.status_code == 429


def test_released_reservation_must_reserve_again(monkeypatch: pytest.MonkeyPatch) -> None:
    balance = _balance(granted=2)
    monkeypatch.setattr(services, "get_or_create_balance", lambda *args, **kwargs: balance)
    existing = UsageReservation(
        id=30,
        public_id="reservation-30",
        account_id=7,
        task_id=20,
        feature="analysis",
        count=1,
        status="released",
    )
    task = Task(id=20, account_id=7, task_type="analysis", input_data={})

    returned = reserve_feature(
        _Session([existing]),  # type: ignore[arg-type]
        _account(),
        task,
        "analysis",
    )
    assert returned is existing
    assert returned.status == "reserved"
    assert balance.reserved == 1


def test_create_task_freezes_refs_without_copying_resource_body() -> None:
    db = _Session()
    task, reservation, existed = create_task(
        db,  # type: ignore[arg-type]
        _account(),
        "analysis",
        {"resume_version_id": "resume-v1", "job_version_id": "job-v2"},
        input_refs=[
            ("resume_version", "resume-v1", 1, "a" * 64),
            ("job_version", "job-v2", 2, "b" * 64),
        ],
    )
    assert reservation is None
    assert existed is False
    assert task.input_data["input_versions"] == [
        {"resource_type": "resume_version", "resource_id": "resume-v1", "version_no": 1},
        {"resource_type": "job_version", "resource_id": "job-v2", "version_no": 2},
    ]
    assert "content" not in task.input_data
    assert sum(isinstance(value, TaskInputRef) for value in db.added) == 2
    assert sum(isinstance(value, TaskOutbox) for value in db.added) == 1


def test_create_task_idempotency_conflict_does_not_create_new_rows() -> None:
    input_data = {"resume_version_id": "resume-v1"}
    existing = Task(
        id=20,
        account_id=7,
        task_type="analysis",
        input_data=input_data,
        idempotency_key="task-1",
        request_hash=payload_hash(input_data),
    )
    db = _Session([7, existing])
    returned, reservation, existed = create_task(
        db,  # type: ignore[arg-type]
        _account(),
        "analysis",
        input_data,
        idempotency_key="task-1",
    )
    assert returned is existing
    assert reservation is None
    assert existed is True
    assert db.added == []

    conflict_db = _Session([7, existing])
    with pytest.raises(DomainError) as error:
        create_task(
            conflict_db,  # type: ignore[arg-type]
            _account(),
            "analysis",
            {"resume_version_id": "different"},
            idempotency_key="task-1",
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert conflict_db.added == []


def test_worker_execution_attempt_does_not_short_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP worker 模式只入队，已认领的独立 Worker 必须继续执行任务。"""

    class ExecutionSession:
        def __init__(self) -> None:
            self.commits = 0
            self.refreshed: list[Any] = []

        def commit(self) -> None:
            self.commits += 1

        def refresh(self, value: Any) -> None:
            self.refreshed.append(value)

    task = Task(id=20, account_id=7, task_type="analysis", input_data={}, status="running")
    attempt = TaskAttempt(id=30, task_id=20, execution_generation=1, status="running")
    db = ExecutionSession()
    completed: list[dict[str, Any]] = []
    published: list[int] = []
    monkeypatch.setattr(services, "settings", SimpleNamespace(execution_mode="worker", model_provider="local", model_name="test"))
    monkeypatch.setattr(services, "finish_task", lambda _db, _task, _reservation, result, **_kwargs: completed.append(result))
    monkeypatch.setattr(services, "mark_outbox_published", lambda _db, task_id: published.append(task_id))

    run_local_task(
        db,  # type: ignore[arg-type]
        task,
        None,
        lambda: {"generated": True},
        attempt=attempt,
        task_result={"resource_type": "analysis", "resource_id": "analysis-1"},
    )

    assert completed == [{"resource_type": "analysis", "resource_id": "analysis-1"}]
    assert published == [20]
    assert db.commits == 2


def test_task_view_only_exposes_safe_result_references() -> None:
    timestamp = datetime(2026, 9, 20, tzinfo=timezone.utc)
    task = Task(
        id=20,
        public_id="task-20",
        account_id=7,
        task_type="analysis",
        status="succeeded",
        current_step="completed",
        progress={"completed": 1, "total": 1},
        input_data={},
        result={
            "resource_type": "analysis",
            "resource_id": "analysis-1",
            "path": "/api/v1/analyses/analysis-1",
            "content": {"resume": "must not be exposed"},
            "internal_trace": "must not be exposed",
        },
        created_at=timestamp,
        updated_at=timestamp,
    )

    view = task_view(task)

    assert view["result"] == {
        "resource_type": "analysis",
        "resource_id": "analysis-1",
        "path": "/api/v1/analyses/analysis-1",
    }
