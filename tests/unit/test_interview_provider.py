from server.app.interview_rubric import (
    FEEDBACK_SCHEMA_VERSION,
    LEGACY_RUBRIC_VERSION,
    SUMMARY_SCHEMA_VERSION,
)
from server.app.model_provider import ModelProvider


def test_followup_feedback_does_not_create_recursive_followup() -> None:
    result = ModelProvider().feedback(
        {"question_text": "请说明你的行动", "question_type": "followup"},
        "太短",
    ).value
    assert result["needs_followup"] is False


def test_local_feedback_explains_star_gaps_and_reanswer_path() -> None:
    result = ModelProvider().feedback(
        {"question_text": "请说明你的项目经历", "question_type": "main"},
        "我用了 TiDB 和 Apifox。",
    ).value
    assert result["schema_version"] == FEEDBACK_SCHEMA_VERSION
    assert result["content"]["summary"]
    assert set(result["content"]["star_assessment"]) == {"situation", "task", "action", "result"}
    assert set(result["content"]["evaluation_dimensions"]) == {"relevance", "specificity", "ownership", "outcome_evidence", "communication"}
    assert result["content"]["priority_actions"]
    assert result["content"]["answer_outline"]
    assert "answer_template" not in result["content"]
    assert result["needs_followup"] is True


def test_summary_counts_only_main_questions() -> None:
    questions = [
        {"id": "main-1", "main_no": 1, "question_type": "main"},
        {"id": "followup-1", "main_no": 1, "question_type": "followup"},
        {"id": "main-2", "main_no": 2, "question_type": "main"},
    ]
    answers = [
        {"question_id": "main-1", "answer_text": "主问题回答"},
        {"question_id": "followup-1", "answer_text": "追问回答"},
    ]
    value = ModelProvider().summary(questions, answers, "early").value
    assert value["schema_version"] == SUMMARY_SCHEMA_VERSION
    assert value["answered_main_count"] == 1
    assert value["answered_followup_count"] == 1
    assert value["unanswered_main_numbers"] == [2]


def test_local_opening_does_not_repeat_one_question_three_times() -> None:
    value = ModelProvider().opening_questions({"dimensions": [{"requirements": [{"job_quote": "验证接口性能"}]}]}, {}).value
    assert len({item["question_text"] for item in value["questions"]}) == 3


def test_summary_ignores_blank_unknown_and_duplicate_followup_answers() -> None:
    value = ModelProvider().summary(
        [{"id": "main-1", "question_type": "main", "main_no": 1}, {"id": "followup-1", "question_type": "followup", "main_no": 1}],
        [{"question_id": "main-1", "answer_text": "  "},
         {"question_id": "followup-1", "answer_text": "补充回答"},
         {"question_id": "followup-1", "answer_text": "补充回答"},
         {"question_id": "unknown", "answer_text": "不属于这场练习"}],
        "early",
    ).value
    assert value["answered_main_count"] == 0
    assert value["answered_followup_count"] == 1
    assert value["unanswered_main_numbers"] == [1]


def test_legacy_pending_tasks_still_use_original_feedback_and_summary() -> None:
    provider = ModelProvider()
    feedback = provider.feedback({"question_type": "main", "rubric_version": LEGACY_RUBRIC_VERSION}, "原版回答").value
    assert feedback["schema_version"] == "interview-feedback-v4"
    assert feedback["content"]["rubric_version"] == LEGACY_RUBRIC_VERSION
    assert feedback["content"]["answer_template"]
    summary = provider.summary([{"id": "q1", "rubric_version": LEGACY_RUBRIC_VERSION}], [], "early").value
    assert summary["schema_version"] == "interview-summary-v2"
