"""明日 SDD 演示切片的关键契约回归测试。"""

from __future__ import annotations

import os
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

from server.app.api import _finish_interview_summary, _pool_by_pk  # noqa: E402
from server.app.errors import NotFoundError  # noqa: E402
from server.app.models import Account, Interview, InterviewAnswer, InterviewQuestion, JobPoolItem  # noqa: E402
from server.app.matching import build_match_result  # noqa: E402


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


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
