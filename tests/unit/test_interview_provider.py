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
    assert result["schema_version"] == "interview-feedback-v3"
    assert result["content"]["summary"]
    assert set(result["content"]["star_assessment"]) == {"situation", "task", "action", "result"}
    assert result["content"]["missing_details"]
    assert "【" in result["content"]["answer_template"]
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
    assert value["answered_main_count"] == 1
    assert value["answered_followup_count"] == 1
    assert value["unanswered_main_numbers"] == [2]
