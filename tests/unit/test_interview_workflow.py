"""冻结岗位依据、完整汇总和 Worker 路径的回归，不连接数据库或模型。"""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from server.app import api as api_module
from server.app.config import settings
from server.app.errors import DomainError
from server.app.interview_rubric import DIMENSIONS, RUBRIC_VERSION, attach_feedback_metadata
from server.app.model_provider import ModelProvider, ModelResult
from server.app.models import (
    Analysis,
    Interview,
    InterviewAnswer,
    InterviewFeedback,
    InterviewQuestion,
    InterviewSummary,
)
from server.app.worker import TaskWorker


def _feedback(level: str) -> dict:
    return attach_feedback_metadata({"evaluation_dimensions": {
        key: {"status": level, "evidence_quote": "我负责接口优化", "feedback": "本题反馈"}
        for key, _ in DIMENSIONS
    }}, "我负责接口优化")


class _Db:
    def __init__(self, pending: bool = False) -> None:
        self.questions = [InterviewQuestion(id=index, public_id=f"q{index}", interview_id=10, account_id=7,
                                           question_type="main", main_no=index, position_no=index, status="answered",
                                           question_text=f"请说明接口优化 {index}", basis={"requirement_ids": ["req-1"]})
                          for index in range(1, 4)]
        self.questions.append(InterviewQuestion(id=4, public_id="followup-1", interview_id=10, account_id=7,
                                               question_type="followup", main_no=1, position_no=4, status="answered",
                                               parent_question_id=1, question_text="请补充优化结果", basis={}))
        if pending:
            self.questions[1].status = "awaiting_answer"
        self.answers = [InterviewAnswer(id=index, account_id=7, interview_id=10, question_id=index, answer_text="我负责接口优化") for index in range(1, 5)]
        self.feedback = {1: _feedback("missing"), 2: _feedback("missing"), 4: _feedback("strong")}
        self.analysis = Analysis(id=20, account_id=7, result={"job_category": "engineering", "dimensions": [{
            "key": "quality", "requirements": [{"requirement_id": "req-1", "job_quote": "负责接口性能优化"}, {"requirement_id": "req-2", "job_quote": "负责组件交付"}],
        }]})
        self.added = []
        self.feedback_sql = ""

    def get(self, model, row_id):
        if model is Analysis:
            return self.analysis
        if model is InterviewQuestion:
            return next((row for row in self.questions if row.id == row_id), None)
        return None

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is InterviewQuestion:
            return SimpleNamespace(all=lambda: self.questions)
        if entity is InterviewAnswer:
            return SimpleNamespace(all=lambda: self.answers)
        if entity is InterviewFeedback:
            self.feedback_sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            # 模拟查询按主问题过滤；同时断言 SQL 确实包含这项条件。
            return SimpleNamespace(all=lambda: [self.feedback[row.id] for row in self.questions if row.question_type == "main" and row.id in self.feedback])
        raise AssertionError(entity)

    def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is InterviewQuestion:
            return next((row.id for row in self.questions if row.question_type == "main" and row.status == "awaiting_answer"), None)
        if entity is InterviewSummary:
            return None
        if entity is InterviewAnswer:
            return self.answers[0]
        raise AssertionError(entity)

    def add(self, item):
        self.added.append(item)

    def flush(self):
        pass


def _interview() -> Interview:
    return Interview(id=10, account_id=7, analysis_id=20, revision=4, status="processing", rubric_version=RUBRIC_VERSION)


@pytest.mark.parametrize("writer", ["api", "worker"])
def test_full_workflow_uses_frozen_basis_and_excludes_followup_scores(monkeypatch, writer: str) -> None:
    db = _Db()
    item = _interview()
    seen_questions = []

    class Provider(ModelProvider):
        def feedback(self, question, answer):
            seen_questions.append(question)
            return ModelResult({"content": _feedback("strong"), "needs_followup": False}, "local", settings.model_name)

    monkeypatch.setattr(api_module, "get_model_provider", Provider)
    generated = api_module._interview_feedback_with_full_summary(db, item, db.questions[2], "我负责接口优化").value
    assert seen_questions[0]["role_requirements"] == [{"requirement_id": "req-1", "text": "负责接口性能优化", "dimension_key": "quality"}]
    assert generated["summary"]["answered_main_count"] == 3
    assert generated["summary"]["answered_followup_count"] == 1
    db.feedback[3] = generated["feedback"]["content"]
    if writer == "api":
        api_module._save_interview_summary(db, item, "full", generated["summary"])
    else:
        TaskWorker(owner="test-worker")._save_summary_value(db, item, "full", generated["summary"])
    assert item.status == "completed"
    assert item.summary["practice_index"] == 33  # 主问题为 0、0、100；追问的 100 不计入。
    assert item.summary["content"]["next_steps"]
    assert item.summary["next_practice_focus"]["suggestion"]
    assert "question_type = 'main'" in db.feedback_sql
    assert "interview_feedback.account_id = 7" in db.feedback_sql


def test_pending_main_question_does_not_generate_full_summary(monkeypatch) -> None:
    db = _Db(pending=True)
    monkeypatch.setattr(api_module, "get_model_provider", ModelProvider)
    value = api_module._interview_feedback_with_full_summary(db, _interview(), db.questions[0], "我负责接口优化和验证，最后通过性能记录确认结果。" * 4).value
    assert "summary" not in value
    assert value["content"]["evaluation_dimensions"]


def test_followup_keeps_parent_question_and_target_requirements() -> None:
    db = _Db()
    value = api_module._interview_question_context(db, _interview(), db.questions[3])
    assert value["parent_question_text"] == db.questions[0].question_text
    assert value["role_requirements"][0]["requirement_id"] == "req-1"
    assert value["parent_answer_text"] == "我负责接口优化"


def test_failed_final_summary_retains_feedback_and_summary_usage(monkeypatch):
    class Provider(ModelProvider):
        def feedback(self, question, answer):
            return ModelResult({"content": _feedback("strong"), "needs_followup": False}, "openai", "test", 12, 8)

        def summary(self, *_):
            error = DomainError("MODEL_OUTPUT_INVALID", "暂未完成", 503)
            error.model_result = ModelResult({}, "openai", "test", 20, 10)
            raise error

    monkeypatch.setattr(api_module, "get_model_provider", Provider)
    db = _Db()
    with pytest.raises(DomainError) as error:
        api_module._interview_feedback_with_full_summary(db, _interview(), db.questions[2], "我负责接口优化")
    assert (error.value.model_result.input_tokens, error.value.model_result.output_tokens) == (32, 18)
    assert not error.value.model_result.usage_incomplete


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_unfinished_analysis_cannot_reserve_interview_usage(monkeypatch, status: str) -> None:
    monkeypatch.setattr(api_module, "_write_guard", lambda *_: None)
    monkeypatch.setattr(api_module, "require_seeker", lambda *_: None)
    monkeypatch.setattr(api_module, "_idempotency_key", lambda *_: "start-key")
    monkeypatch.setattr(api_module, "_pool", lambda *_: SimpleNamespace(id=3))
    monkeypatch.setattr(api_module, "_analysis", lambda *_: SimpleNamespace(status=status, result={"dimensions": [{}]}))
    monkeypatch.setattr(api_module, "_version", lambda *_: SimpleNamespace(id=51))
    monkeypatch.setattr(api_module, "create_task", lambda *_args, **_kwargs: pytest.fail("未完成报告不应创建收费任务"))
    payload = api_module.InterviewStartRequest(job_pool_item_id="p1", analysis_id="a1", resume_document_version_id="r1", confirm_usage=True)
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/interviews", "headers": []})
    with pytest.raises(DomainError) as error:
        api_module.start_interview(payload, request, SimpleNamespace(id=7), None)
    assert error.value.code == "INTERVIEW_SOURCE_UNAVAILABLE"
