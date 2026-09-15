"""Purslyx 核心业务的关键契约回归测试。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

# API 模块会在导入时创建 PostgreSQL Engine；单元测试只需要合法的 201 URL，
# 不建立连接，也不把密码写入测试代码。
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test-user@10.10.10.201:5432/purslyx")

from server.app.config import settings  # noqa: E402

# 其他纯单元测试可能先导入 config，令全局 Settings 已经按空环境构造；
# 这里只补合法的 201 URL，API 导入不会建立网络连接。
if not settings.database_url:
    object.__setattr__(settings, "database_url", os.environ["DATABASE_URL"])

from server.app import api as api_module  # noqa: E402
from server.app.api import (  # noqa: E402
    _escape_csv_formula,
    _finish_interview_summary,
    _log_export_datetime,
    _log_export_view,
    _pool_by_pk,
)
from server.app.errors import DomainError, NotFoundError  # noqa: E402
from server.app.main import app  # noqa: E402
from server.app.matching import build_match_result  # noqa: E402
from server.app.models import (  # noqa: E402
    Interview,
    InterviewAnswer,
    InterviewQuestion,
    InterviewSummary,
    JobPoolItem,
    LogExport,
    StoredFile,
)


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


def test_anonymous_demo_api_is_not_registered() -> None:
    """所有业务功能都必须通过带认证的正式 API 进入。"""

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert app.title == "Purslyx API"
    assert not any(path.startswith("/api/v1/demo") for path in paths)


class _InterviewSession:
    """只实现总结函数需要的读取和写入面，避免测试依赖真实数据库。"""

    def __init__(self, questions: list[InterviewQuestion], answers: list[InterviewAnswer]) -> None:
        self.questions = questions
        self.answers = answers
        self.added: list[Any] = []

    def scalars(self, statement: Any) -> _Rows:
        entity = statement.column_descriptions[0]["entity"]
        if entity is InterviewQuestion:
            return _Rows(self.questions)
        if entity is InterviewAnswer:
            return _Rows(self.answers)
        raise AssertionError(f"未预期的查询实体：{entity}")

    def scalar(self, statement: Any) -> None:
        entity = statement.column_descriptions[0]["entity"]
        if entity is InterviewSummary:
            return None
        raise AssertionError(f"未预期的查询实体：{entity}")

    def get(self, model: Any, row_id: int) -> Any:
        if model is InterviewQuestion:
            return next(row for row in self.questions if row.id == row_id)
        raise AssertionError(f"未预期的 get 实体：{model}")

    def add(self, value: Any) -> None:
        self.added.append(value)


@pytest.mark.parametrize(
    ("completion_type", "expected_status"),
    [("full", "completed"), ("early", "ended_early")],
)
def test_interview_summary_full_and_early_contract(completion_type: str, expected_status: str) -> None:
    question = InterviewQuestion(
        id=11,
        public_id="question-1",
        account_id=7,
        interview_id=3,
        question_type="main",
        main_no=1,
        position_no=1,
        question_text="请介绍相关经历",
        basis={},
        status="answered",
    )
    answer = InterviewAnswer(
        id=21,
        public_id="answer-1",
        account_id=7,
        interview_id=3,
        question_id=11,
        answer_text="我负责需求拆解、组件实现和上线验证。",
    )
    item = Interview(
        id=3,
        public_id="interview-1",
        account_id=7,
        job_pool_item_id=31,
        analysis_id=41,
        resume_version_id=51,
        title="契约测试",
        status="awaiting_answer",
        revision=2,
    )
    db = _InterviewSession([question], [answer])

    _finish_interview_summary(db, SimpleNamespace(id=7), item, completion_type)  # type: ignore[arg-type]

    summary = db.added[0]
    assert summary.completion_type == completion_type
    assert summary.content["completion_type"] == completion_type
    assert item.status == expected_status


class _PoolSession:
    def __init__(self, result: JobPoolItem | None) -> None:
        self.result = result
        self.statement: Any | None = None

    def scalar(self, statement: Any) -> JobPoolItem | None:
        self.statement = statement
        return self.result


def test_variant_pool_lookup_uses_internal_pk_with_isolation_predicates() -> None:
    pool = JobPoolItem(id=42, public_id="pool-public-id", account_id=7, job_fields={})
    db = _PoolSession(pool)

    assert _pool_by_pk(db, account_id=7, pool_id=42) is pool
    sql = str(db.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "job_pool_items.id = 42" in sql
    assert "job_pool_items.account_id = 7" in sql
    assert "job_pool_items.deleted_at IS NULL" in sql

    missing = _PoolSession(None)
    with pytest.raises(NotFoundError):
        _pool_by_pk(missing, account_id=7, pool_id=42)


def test_match_report_exposes_verification_items_and_targeted_questions() -> None:
    report = build_match_result(
        {
            "schema_version": "document-content-v1",
            "sections": [{"section_key": "experience", "position": 1, "segments": [{"segment_key": "exp-1", "text": "负责 React 前端开发。"}]}],
        },
        {
            "schema_version": "document-content-v1",
            "job_fields": {"title": "前端开发工程师", "requirements": ["熟悉 React 和 TypeScript", "需要带领跨团队项目交付"]},
        },
        None,
    )

    assert report["verification_items"]
    assert any(item["kind"] == "condition" for item in report["verification_items"])
    assert len(report["interview_questions"]) == 2
    assert report["interview_questions"][0]["basis"]["rule_version"] == "interview-question-basis-v1"


def test_log_export_helpers_parse_time_and_escape_csv_formulas() -> None:
    parsed = _log_export_datetime({"created_from": "2026-09-15T08:00:00"}, "created_from")
    assert parsed == datetime(2026, 9, 15, 8, tzinfo=timezone.utc)
    assert _escape_csv_formula("=HYPERLINK(\"https://example.test\")") == "'=HYPERLINK(\"https://example.test\")"
    assert _escape_csv_formula("ordinary action") == "ordinary action"

    with pytest.raises(DomainError) as error:
        _log_export_datetime({"created_to": "not-a-time"}, "created_to")
    assert error.value.code == "LOG_RANGE_INVALID"


class _ExportWorkSession:
    pass


def test_log_export_work_freezes_filters_and_writes_relative_csv(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_log_items(db: Any, log_type: str, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        captured["log_type"] = log_type
        captured.update(kwargs)
        return (
            [
                {
                    "id": "audit-1",
                    "category": "operations",
                    "action": "=HYPERLINK(\"https://example.test\")",
                    "outcome": "succeeded",
                    "created_at": "2026-09-15T08:00:00+00:00",
                }
            ],
            {"has_more": False},
        )

    export_settings = SimpleNamespace(export_dir=tmp_path / "exports", data_dir=tmp_path)
    monkeypatch.setattr(api_module, "_log_items", fake_log_items)
    monkeypatch.setattr(api_module, "settings", export_settings)
    monkeypatch.setattr(api_module, "ensure_storage_capacity", lambda db, incoming_bytes: None)

    item = LogExport(
        public_id="export-1",
        account_id=7,
        log_type="operations",
        filters={
            "export_format": "csv",
            "created_from": "2026-09-15T07:00:00Z",
            "created_to": "2026-09-15T09:00:00Z",
        },
        status="exporting",
    )
    result = api_module._log_export_work(_ExportWorkSession(), item)

    assert captured["log_type"] == "operations"
    assert captured["created_from"] == datetime(2026, 9, 15, 7, tzinfo=timezone.utc)
    assert captured["created_to"] == datetime(2026, 9, 15, 9, tzinfo=timezone.utc)
    assert result["row_count"] == 1
    assert result["path"].startswith("exports/")
    assert str(tmp_path) not in result["path"]
    output = (tmp_path / result["path"]).read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in output


class _ExpireQuery:
    def __init__(self) -> None:
        self.updated = False

    def filter(self, *conditions: Any) -> "_ExpireQuery":
        return self

    def update(self, values: Any, **kwargs: Any) -> int:
        self.updated = True
        return 1


class _ExpireSession:
    def __init__(self) -> None:
        self.query_value = _ExpireQuery()
        self.committed = False

    def query(self, model: Any) -> _ExpireQuery:
        assert model is StoredFile
        return self.query_value

    def commit(self) -> None:
        self.committed = True


def test_log_export_expiration_revokes_file_record() -> None:
    item = LogExport(
        public_id="export-1",
        account_id=7,
        log_type="operations",
        filters={"export_format": "jsonl"},
        status="downloadable",
        file_path="exports/log-1.jsonl",
    )
    db = _ExpireSession()

    api_module._expire_log_export(db, item)

    assert item.status == "expired"
    assert db.query_value.updated is True
    assert db.committed is True


def test_worker_log_export_handler_finishes_downloadable(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.app import worker as worker_module

    item = LogExport(
        public_id="export-1",
        account_id=7,
        task_id=3,
        log_type="operations",
        filters={"export_format": "jsonl"},
        status="queued",
    )
    task = SimpleNamespace(id=3, account_id=7, public_id="task-1", input_data={})
    attempt = SimpleNamespace(id=4)

    class WorkerSession:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def scalar(self, statement: Any) -> LogExport:
            return item

        def add(self, value: Any) -> None:
            self.added.append(value)

    def fake_run_model(db: Any, task_value: Any, attempt_value: Any, reservation: Any, work: Any, **kwargs: Any) -> None:
        assert task_value is task
        assert attempt_value is attempt
        kwargs["on_success"](work())

    monkeypatch.setattr(worker_module, "_run_model", fake_run_model)
    monkeypatch.setattr(
        api_module,
        "_log_export_work",
        lambda db, export: {"path": "exports/log-1.jsonl", "row_count": 1, "byte_size": 8, "sha256": "a" * 64},
    )
    db = WorkerSession()

    worker_module.TaskWorker(owner="test-worker")._handle_log_export(db, task, attempt, None)

    assert item.status == "downloadable"
    assert item.file_path == "exports/log-1.jsonl"
    assert item.expires_at is not None
    assert any(isinstance(value, StoredFile) for value in db.added)


def test_log_export_view_does_not_expose_private_file_metadata() -> None:
    item = LogExport(
        public_id="export-1",
        account_id=7,
        task_id=None,
        log_type="operations",
        filters={"export_format": "jsonl", "row_count": 2, "created_from": "2026-09-15T00:00:00Z"},
        status="downloadable",
        file_path="exports/private.jsonl",
        created_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )

    view = _log_export_view(_ExportWorkSession(), item)

    assert view["status"] == "downloadable"
    assert view["row_count"] == 2
    assert "file_path" not in view
    assert "sha256" not in view
