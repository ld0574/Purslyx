"""Worker 迟到回写与取消边界。"""

from __future__ import annotations

from typing import Any

from server.app import worker as worker_module
from server.app.models import Task, TaskAttempt
from server.app.worker import TaskWorker


class _Session:
    def __init__(self, task: Task, attempt: TaskAttempt) -> None:
        self.task = task
        self.attempt = attempt
        self.committed = False

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get(self, model: type, _: int) -> Any:
        if model is Task:
            return self.task
        if model is TaskAttempt:
            return self.attempt
        return None

    def commit(self) -> None:
        self.committed = True


def test_late_worker_failure_does_not_change_cancelled_business_state(monkeypatch: Any) -> None:
    task = Task(id=41, account_id=7, task_type="analysis", input_data={}, status="cancelled")
    attempt = TaskAttempt(id=51, task_id=41, execution_generation=1, status="cancelled")
    db = _Session(task, attempt)
    business_failures: list[int] = []
    published: list[int] = []

    monkeypatch.setattr(worker_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker_module, "_reservation", lambda *_: None)
    monkeypatch.setattr(worker_module, "fail_task", lambda *_args, **_kwargs: task)
    monkeypatch.setattr(worker_module, "mark_outbox_published", lambda _db, task_id: published.append(task_id))

    worker = TaskWorker(owner="late-worker")
    monkeypatch.setattr(worker, "_mark_business_failed", lambda _db, value, _error: business_failures.append(value.id))
    worker._record_failure(task_id=task.id, attempt_id=attempt.id, error=RuntimeError("late"))

    assert task.status == "cancelled"
    assert business_failures == []
    assert published == [task.id]
    assert db.committed is True
