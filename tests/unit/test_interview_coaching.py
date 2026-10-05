"""新版可信评价、专业核查、修复消耗与独立训练的回归。全部使用合成回答。"""

from copy import deepcopy

import pytest

from server.app import api as api_module
from server.app.errors import DomainError
from server.app.interview_rubric import (
    DIMENSIONS,
    FEEDBACK_SCHEMA_VERSION,
    RUBRIC_VERSION,
    compare_practice,
    validate_v2_content,
)
from server.app.model_provider import ModelProvider, ModelResult, OpenAIModelProvider
from server.app.models import (
    Account,
    Analysis,
    Document,
    DocumentVersion,
    Interview,
    InterviewAnswer,
    InterviewFeedback,
    InterviewPractice,
    InterviewQuestion,
    JobPoolItem,
    Task,
)
from server.app.worker import SUPPORTED_TASK_TYPES, TaskWorker

ANSWER = "我会比较隔离方案，先明确共享实例和资源约束，再通过故障注入与监控验证风险。"
QUESTION = {
    "question_type": "main",
    "question_kind": "reasoning",
    "question_text": "如何选择隔离方案？",
    "rubric_version": RUBRIC_VERSION,
}


def raw_feedback(status="partial", answer=ANSWER):
    return {
        "summary": "回答包含判断与验证线索。请说明方案取舍。",
        "evaluation_dimensions": {
            key: {
                "status": status,
                "feedback": "请补充对应依据。",
                "evidence_quote": answer if status != "missing" else None,
            }
            for key, _ in DIMENSIONS
        },
        "followup_review": None,
        "knowledge_checks": [],
        "answer_outline": [{"kind": "quote", "label": "依据", "text": answer}],
        "star_assessment": None,
    }


@pytest.mark.parametrize("kind", ["experience", "reasoning", "scenario"])
def test_frozen_kind_controls_structure_and_verification_not_historical_achievements(kind):
    content = ModelProvider().feedback({**QUESTION, "question_kind": kind}, ANSWER).value["content"]
    assert content["question_kind"] == kind
    assert bool(content["star_assessment"]) == (kind == "experience")
    assert content["evaluation_dimensions"]["outcome_evidence"]["status"] != "missing"
    assert "answer_template" not in content
    assert all(
        row["kind"] == "prompt" or row["text"] in ANSWER for row in content["answer_outline"]
    )
    if kind != "experience":
        assert "历史业绩" in content["evaluation_dimensions"]["outcome_evidence"]["feedback"]


@pytest.mark.parametrize("bad", [None, [], {}, "invalid", 100])
def test_malformed_status_is_system_error_not_user_missing(bad):
    raw = raw_feedback()
    raw["evaluation_dimensions"]["ownership"]["status"] = bad
    with pytest.raises(DomainError) as error:
        validate_v2_content(raw, QUESTION, ANSWER)
    assert error.value.code == "INTERVIEW_EVALUATION_INVALID"
    assert "评价暂未完成" in error.value.message


def test_long_quote_validated_in_full_before_display_truncation():
    answer = ANSWER * 12
    raw = raw_feedback(answer=answer)
    assert (
        len(
            validate_v2_content(raw, QUESTION, answer)["evaluation_dimensions"]["relevance"][
                "evidence_quote"
            ]
        )
        == 300
    )
    raw["evaluation_dimensions"]["relevance"]["evidence_quote"] = answer + "伪造后缀"
    with pytest.raises(DomainError):
        validate_v2_content(raw, QUESTION, answer)


def test_real_missing_is_valid_and_does_not_require_invented_evidence():
    raw = raw_feedback("missing", answer="不知道")
    content = validate_v2_content(raw, QUESTION, "不知道")
    assert content["review_status"] == "verified"
    assert all(
        row["status"] == "missing" and row["evidence_quote"] is None
        for row in content["evaluation_dimensions"].values()
    )


@pytest.mark.parametrize("field", ["evaluation_dimensions", "knowledge_checks", "answer_outline"])
def test_any_displayed_evidence_requires_real_quote(field):
    raw = raw_feedback()
    if field == "evaluation_dimensions":
        raw[field]["specificity"]["evidence_quote"] = "我完成了千万级系统迁移"
    elif field == "knowledge_checks":
        raw[field] = [
            {"claim_quote": "原文不存在的论断", "note": "需核实", "verification": "请说明核验方式"}
        ]
    else:
        raw[field][0]["text"] = "我完成了千万级系统迁移"
    with pytest.raises(DomainError):
        validate_v2_content(raw, QUESTION, ANSWER)


def test_followup_uses_parent_context_without_double_scoring_or_misattribution():
    raw = {
        **raw_feedback(),
        "evaluation_dimensions": None,
        "answer_outline": [],
        "followup_review": {
            "supplemented": [
                {"evidence_source": "parent_answer", "quote": ANSWER},
                {"evidence_source": "answer", "quote": "请用告警记录复核"},
            ],
            "remaining_questions": ["请说明验证范围。"],
        },
    }
    question = {**QUESTION, "question_type": "followup", "parent_answer_text": ANSWER}
    content = validate_v2_content(raw, question, "请用告警记录复核")
    assert content["evaluation_dimensions"] is None
    assert content["followup_review"]["supplemented"][0]["evidence_source"] == "parent_answer"
    raw["followup_review"]["supplemented"][0]["evidence_source"] = "answer"
    with pytest.raises(DomainError):
        validate_v2_content(raw, question, "请用告警记录复核")


@pytest.mark.parametrize(
    "bad_text",
    ["我负责全量迁移并完成上线。", "我用了独立实例取得可靠隔离。", "I used a separate production cluster.", "你已经将耗时降低了 99%。", "据权威论文 [1] 已验证可靠。"],
)
def test_generated_feedback_cannot_invent_experience_metrics_or_references(bad_text):
    raw = raw_feedback()
    raw["knowledge_checks"] = [
        {"claim_quote": ANSWER, "note": bad_text, "verification": "请核实隔离边界。"}
    ]
    with pytest.raises(DomainError):
        validate_v2_content(raw, QUESTION, ANSWER)


def test_technical_diagnosis_flags_runtime_name_and_logical_vs_fault_isolation():
    answer = "我选择 gVisor（runc）作为运行时。Redis 的 DB 和 Key 前缀保证故障隔离。"
    content = ModelProvider().feedback(QUESTION, answer).value["content"]
    assert len(content["knowledge_checks"]) == 2
    assert all(
        row["claim_quote"] in answer and row["status"] == "needs_verification"
        for row in content["knowledge_checks"]
    )
    assert "runsc" in content["knowledge_checks"][0]["note"]
    assert "故障隔离" in content["knowledge_checks"][1]["note"]
    assert all(row["status"] != "strong" for row in content["evaluation_dimensions"].values())


def test_verified_first_person_quote_is_allowed_but_outline_keeps_original_anchor_when_empty():
    raw = raw_feedback()
    raw["summary"] = f"‘{ANSWER}’包含判断与验证线索。"
    raw["answer_outline"] = []
    content = validate_v2_content(raw, QUESTION, ANSWER)
    assert content["summary"] == raw["summary"]
    assert content["answer_outline"][0] == {"kind": "quote", "label": "原文", "text": ANSWER}
    assert any(row["kind"] == "prompt" for row in content["answer_outline"])


@pytest.mark.parametrize("repairs", [True, False])
def test_one_automatic_repair_accumulates_usage_and_never_returns_zero_for_invalidity(repairs):
    provider = object.__new__(OpenAIModelProvider)
    calls = []

    def generate(*_):
        raw = raw_feedback()
        if not calls or not repairs:
            raw["evaluation_dimensions"]["ownership"]["evidence_quote"] = "虚构原文"
        calls.append(raw)
        return ModelResult(
            {"content": raw, "needs_followup": False, "followup_question": None},
            "openai",
            "test",
            13,
            7,
        )

    provider._json = generate
    if repairs:
        result = provider.feedback(QUESTION, ANSWER)
        assert (result.input_tokens, result.output_tokens) == (26, 14)
        assert result.value["content"]["schema_version"] == FEEDBACK_SCHEMA_VERSION
    else:
        with pytest.raises(DomainError) as error:
            provider.feedback(QUESTION, ANSWER)
        assert (error.value.model_result.input_tokens, error.value.model_result.output_tokens) == (
            26,
            14,
        )
        assert error.value.code == "INTERVIEW_EVALUATION_INVALID"
    assert len(calls) == 2


def test_comparison_reports_improvement_decline_and_absent_baseline_honestly():
    old = validate_v2_content(raw_feedback(), QUESTION, ANSWER)
    newer = deepcopy(old)
    newer["evaluation_dimensions"]["ownership"]["status"] = "missing"
    newer["evaluation_dimensions"]["specificity"]["status"] = "strong"
    comparison = compare_practice(old, newer)
    assert comparison["baseline_available"] is True
    assert comparison["dimensions"]["ownership"]["change"] == "weaker"
    assert comparison["dimensions"]["specificity"]["change"] == "improved"
    assert comparison["dimensions"]["relevance"]["change"] == "unchanged"
    old["schema_version"] = "interview-feedback-v4"
    assert compare_practice(old, newer)["baseline_available"] is False
    assert all(
        row["change"] == "unavailable"
        for row in compare_practice(None, newer)["dimensions"].values()
    )


class PracticeDb:
    def __init__(self):
        self.interview = Interview(
            id=1,
            public_id="i1",
            account_id=7,
            analysis_id=2,
            resume_version_id=3,
            job_pool_item_id=4,
            status="completed",
            rubric_version=RUBRIC_VERSION,
            summary={"practice_index": 50},
            revision=9,
        )
        self.question = InterviewQuestion(
            id=5,
            public_id="q1",
            interview_id=1,
            account_id=7,
            question_type="main",
            question_text=QUESTION["question_text"],
            basis={"question_kind": "reasoning"},
        )
        self.original = InterviewAnswer(
            id=6, interview_id=1, account_id=7, question_id=5, answer_text="正式回答不改变"
        )
        self.sources = {
            Account: Account(id=7, status="active"),
            Analysis: Analysis(id=2, account_id=7, result={}),
            DocumentVersion: DocumentVersion(id=3, account_id=7, document_id=8),
            JobPoolItem: JobPoolItem(id=4, account_id=7),
            Document: Document(id=8, account_id=7),
        }
        self.sources.update({InterviewQuestion: self.question, InterviewAnswer: self.original})
        self.item = InterviewPractice(
            id=9,
            public_id="practice1",
            account_id=7,
            interview_id=1,
            question_id=5,
            original_answer_id=6,
            answer_text=ANSWER,
            rubric_version=RUBRIC_VERSION,
            status="queued",
        )
        self.feedback = InterviewFeedback(
            content=validate_v2_content(raw_feedback(), QUESTION, ANSWER)
        )

    def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return (
            self.interview
            if entity is Interview
            else self.feedback
            if entity is InterviewFeedback
            else 0
        )

    def get(self, model, _, **__):
        return self.sources.get(model)

    def refresh(self, _):
        pass


def test_independent_practice_has_no_usage_reservation_or_parent_writes(monkeypatch):
    db = PracticeDb()
    task = Task(
        id=10,
        public_id="t1",
        account_id=7,
        input_data={"rubric_version": RUBRIC_VERSION},
        status="queued",
    )
    options = {}

    def run(_db, _task, reservation, work, **kwargs):
        assert reservation is None
        options.update(kwargs)
        kwargs["on_success"](work().value)

    monkeypatch.setattr(api_module, "run_local_task", run)
    monkeypatch.setattr(api_module, "get_model_provider", ModelProvider)
    api_module._run_interview_practice(db, db.item, task)
    assert db.item.status == "available"
    assert db.interview.status == "completed" and db.interview.revision == 9
    assert db.interview.summary == {"practice_index": 50}
    assert db.original.answer_text == "正式回答不改变"
    assert "feature" not in options and options["cost_feature"] == "interview_practice"
    assert options["estimated_output_tokens"] == 12000
    assert options["task_result"]["interview_id"] == "i1"


@pytest.mark.parametrize(
    "deleted", ["practice", "interview", "analysis", "resume", "document", "pool"]
)
def test_deleted_source_cannot_be_practiced_or_retried(deleted):
    db = PracticeDb()
    row = {
        "practice": db.item,
        "interview": db.interview,
        "analysis": db.sources[Analysis],
        "resume": db.sources[DocumentVersion],
        "document": db.sources[Document],
        "pool": db.sources[JobPoolItem],
    }[deleted]
    row.deleted_at = "deleted"
    with pytest.raises(DomainError):
        api_module._practice_source(db, db.item, lock=True)


def test_training_task_failure_does_not_mark_parent_failed():
    db = PracticeDb()
    db.scalar = lambda _: db.item
    task = Task(
        account_id=7, task_type="interview_practice", input_data={"practice_id": "practice1"}
    )
    TaskWorker(owner="test")._mark_business_failed(db, task, RuntimeError("private answer"))
    assert "interview_practice" in SUPPORTED_TASK_TYPES
    assert db.item.status == "failed"
    assert db.interview.status == "completed" and db.interview.summary == {"practice_index": 50}


def test_feedback_text_is_chinese_but_original_technical_quotes_unchanged():
    raw = raw_feedback()
    raw["summary"] = "Ownership partial，需补充依据。"
    content = validate_v2_content(raw, QUESTION, ANSWER)
    assert "Ownership" not in content["summary"] and "partial" not in content["summary"]
    assert content["evaluation_dimensions"]["ownership"]["evidence_quote"] == ANSWER


def test_suspended_or_cross_account_source_does_not_allow_training():
    db = PracticeDb()
    db.sources[Account].status = "suspended"
    with pytest.raises(DomainError):
        api_module._practice_source(db, db.item)
    db.sources[Account].status = "active"
    db.question.account_id = 77
    with pytest.raises(DomainError):
        api_module._practice_source(db, db.item)
