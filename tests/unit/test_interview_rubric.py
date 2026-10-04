import pytest

from server.app.interview_rubric import (
    FEEDBACK_SCHEMA_VERSION,
    RUBRIC_VERSION,
    aggregate_main_feedback,
    attach_feedback_metadata,
    enrich_summary,
    local_dimensions,
)


def _feedback(levels: list[str]) -> dict:
    keys = ["relevance", "specificity", "ownership", "outcome_evidence", "communication"]
    return {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "evaluation_dimensions": {
            key: {"status": level, "feedback": "反馈", "evidence_quote": "本人回答原文" if level != "missing" else None}
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
    assert content["evaluation_dimensions"]["specificity"]["status"] == "missing"


def test_complete_main_feedback_uses_equal_weighted_fixed_scores() -> None:
    result = aggregate_main_feedback([
        _feedback(["strong", "partial", "strong", "missing", "partial"]),
        _feedback(["strong", "strong", "partial", "partial", "partial"]),
        _feedback(["partial", "strong", "partial", "strong", "strong"]),
    ], "full")
    assert result["evaluation_dimensions"]["relevance"]["score"] == 83
    assert result["evaluation_dimensions"]["outcome_evidence"]["score"] == 50
    assert result["practice_index"] == 70
    assert result["next_practice_focus"]["key"] == "outcome_evidence"
    assert "核验" in result["next_practice_focus"]["suggestion"]


def test_early_or_legacy_feedback_does_not_produce_index() -> None:
    assert aggregate_main_feedback([_feedback(["strong"] * 5)] * 3, "early")["practice_index"] is None
    assert aggregate_main_feedback([{"star_assessment": {}}] * 3, "full")["practice_index"] is None


def test_evidence_is_validated_before_truncation_and_missing_has_no_quote() -> None:
    answer = "真实回答" * 100
    content = attach_feedback_metadata({"evaluation_dimensions": {
        "specificity": {"status": "strong", "evidence_quote": answer[:300] + "编造的经历"},
        "ownership": {"status": "missing", "evidence_quote": answer[:20]},
    }}, answer)
    assert content["evaluation_dimensions"]["specificity"]["status"] == "missing"
    assert content["evaluation_dimensions"]["specificity"]["evidence_quote"] is None
    assert content["evaluation_dimensions"]["ownership"]["evidence_quote"] is None


def test_practice_index_does_not_average_rounded_display_values() -> None:
    result = aggregate_main_feedback([
        _feedback(["partial", "partial", "partial", "partial", "missing"]),
        _feedback(["missing"] * 5),
        _feedback(["missing"] * 5),
    ], "full")
    assert result["evaluation_dimensions"]["relevance"]["score"] == 17
    assert result["practice_index"] == 13  # 200 / 15，而不是 (17 * 4) / 5。


@pytest.mark.parametrize("bad_dimension", [None, "strong", {}, {"status": "invalid"}, {"status": []}, {"status": "strong", "evidence_quote": None}])
def test_incomplete_or_malformed_feedback_is_not_scored(bad_dimension) -> None:
    feedback = _feedback(["strong"] * 5)
    feedback["evaluation_dimensions"]["relevance"] = bad_dimension
    assert aggregate_main_feedback([feedback] * 3, "full")["practice_index"] is None


def test_mixed_schema_extra_feedback_and_incomplete_answers_are_not_scored() -> None:
    feedback = _feedback(["strong"] * 5)
    legacy = {**feedback, "schema_version": "interview-feedback-v3"}
    assert aggregate_main_feedback([feedback, feedback, legacy], "full")["practice_index"] is None
    assert aggregate_main_feedback([feedback] * 3 + [{}], "full")["practice_index"] is None
    assert enrich_summary({"answered_main_count": 2}, [feedback] * 3, "full")["practice_index"] is None


def test_local_feedback_cannot_award_strong_for_length_or_numeric_dates() -> None:
    dimensions = local_dimensions("2020 年入职。" + "我喜欢美食和旅游。" * 30, "请说明数据库性能优化方案")
    assert all(item["status"] != "strong" for item in dimensions.values())
    assert dimensions["relevance"]["status"] == "missing"
    assert dimensions["outcome_evidence"]["status"] == "missing"


def test_rejected_model_evidence_cannot_turn_into_a_zero_index() -> None:
    content = attach_feedback_metadata({"evaluation_dimensions": {
        "relevance": {"status": "strong", "evidence_quote": "编造的依据"},
    }}, "本人回答原文")
    assert content["evaluation_dimensions"]["relevance"]["evidence_status"] == "unverified"
    assert attach_feedback_metadata(content, "本人回答原文")["evaluation_dimensions"]["relevance"]["evidence_status"] == "unverified"
    assert aggregate_main_feedback([content] * 3, "full")["practice_index"] is None
