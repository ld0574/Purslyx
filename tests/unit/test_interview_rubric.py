from server.app.interview_rubric import (
    FEEDBACK_SCHEMA_VERSION,
    RUBRIC_VERSION,
    aggregate_main_feedback,
    attach_feedback_metadata,
)


def _feedback(levels: list[str]) -> dict:
    keys = ["relevance", "specificity", "ownership", "outcome_evidence", "communication"]
    return {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "evaluation_dimensions": {
            key: {"status": level, "feedback": "反馈", "evidence_quote": None}
            for key, level in zip(keys, levels, strict=True)
        },
    }


def test_feedback_evidence_must_be_exact_answer_text() -> None:
    content = attach_feedback_metadata({
        "evaluation_dimensions": {
            "relevance": {"status": "strong", "feedback": "切题", "evidence_quote": "我负责接口优化"},
            "specificity": {"status": "partial", "feedback": "有细节", "evidence_quote": "模型改写的假证据"},
        },
    }, "我负责接口优化，并把耗时降低了 30%。")
    assert content["evaluation_dimensions"]["relevance"]["evidence_quote"] == "我负责接口优化"
    assert content["evaluation_dimensions"]["specificity"]["evidence_quote"] is None


def test_complete_main_feedback_uses_equal_weighted_fixed_scores() -> None:
    result = aggregate_main_feedback([
        _feedback(["strong", "partial", "strong", "missing", "partial"]),
        _feedback(["strong", "strong", "partial", "partial", "partial"]),
        _feedback(["partial", "strong", "partial", "strong", "strong"]),
    ], "full")
    assert result["evaluation_dimensions"]["relevance"]["score"] == 83
    assert result["evaluation_dimensions"]["outcome_evidence"]["score"] == 50
    assert result["practice_index"] == 70


def test_early_or_legacy_feedback_does_not_produce_index() -> None:
    assert aggregate_main_feedback([_feedback(["strong"] * 5)] * 3, "early")["practice_index"] is None
    assert aggregate_main_feedback([{"star_assessment": {}}] * 3, "full")["practice_index"] is None
