import pytest

from server.app.matching import (
    SCORING_RULE_VERSION,
    _requirements,
    build_match_result,
    compare_conditions,
    compare_salary,
    merge_preference_contents,
)


def _salary(minimum: int, maximum: int, **overrides: object) -> dict:
    value = {
        "status": "specified",
        "min": str(minimum),
        "max": str(maximum),
        "currency": "CNY",
        "period": "monthly",
        "tax_basis": "gross",
        "salary_months": 12,
    }
    value.update(overrides)
    return value


def test_matching_keeps_unknown_salary_unknown() -> None:
    result = compare_salary(
        {
            "status": "specified",
            "min": "18000",
            "max": "28000",
            "currency": "CNY",
            "period": "monthly",
            "tax_basis": "gross",
        },
        {
            "status": "specified",
            "min": "18000",
            "max": "28000",
            "currency": None,
            "period": "monthly",
            "tax_basis": None,
        },
    )
    assert result["status"] == "unknown"


def test_match_result_has_evidence_and_code_score() -> None:
    resume = {
        "sections": [
            {
                "segments": [
                    {"segment_key": "work-1", "text": "使用 React 和 TypeScript 完成前端项目开发"}
                ]
            }
        ],
        "explicit_gaps": [],
    }
    job = {
        "job_fields": {
            "title": "前端开发工程师",
            "category": "engineering",
            "requirements": ["熟悉 React 和 TypeScript", "负责前端项目开发"],
        }
    }
    result = build_match_result(resume, job)
    assert result["ability_score"] is not None
    assert result["evidence_coverage"] is not None
    assert any(item["requirements"] for item in result["dimensions"])


def test_unknown_requirements_do_not_render_as_zero_score() -> None:
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "work-1", "text": "负责后端服务维护"}]}]},
        {
            "job_fields": {
                "title": "大模型应用工程师",
                "category": "engineering",
                "requirements": ["熟悉量化交易风控模型与 CUDA 优化"],
            }
        },
    )

    assert result["ability_score"] is None
    assert result["evidence_coverage"] == 0.0
    assert result["overall_advice"]["status"] == "needs_more_information"


def test_model_unknown_can_fall_back_to_partial_verified_evidence() -> None:
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "work-1", "text": "负责后端服务开发与项目交付"}]}]},
        {
            "job_fields": {
                "title": "后端工程师",
                "category": "engineering",
                "requirements": ["负责后端服务开发"],
            }
        },
        ai_findings={
            "requirements": [{
                "requirement_id": "req-1",
                "dimension_key": "technical",
                "status": "needs_confirmation",
                "evidence_segment_keys": [],
                "explanation": "模型未提交引用。",
            }],
            "insights": {},
        },
    )

    requirement = next(item for dimension in result["dimensions"] for item in dimension["requirements"])
    assert requirement["status"] == "partially_supported"
    assert requirement["evidence"]
    assert requirement["match_score"] == 50.0


def test_matching_includes_duties_and_deduplicates_requirements_with_stable_ids() -> None:
    fields = {
        "requirements": ["熟悉 React", {"text": "熟悉 TypeScript", "requirement_id": "req-1"}],
        "responsibilities": "熟悉 React\n负责性能优化\n负责组件交付",
    }
    rows = _requirements(fields)
    assert [row["text"] for row in rows] == ["熟悉 React", "熟悉 TypeScript", "负责性能优化", "负责组件交付"]
    assert [row["requirement_id"] for row in rows] == ["req-1", "req-2", "req-3", "req-4"]
    assert rows[2]["source_type"] == "responsibility"
    assert rows == _requirements(fields)
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "s1", "text": "使用 React 完成组件交付和性能优化"}]}]},
        {"job_fields": {**fields, "category": "engineering"}},
    )
    assert sum(len(item["requirements"]) for item in result["dimensions"]) == 4
    quality = next(item for item in result["dimensions"] if item["key"] == "quality")
    assert quality["requirements"][0]["job_quote"] == "负责性能优化"
    assert result["scoring_rule_version"] == SCORING_RULE_VERSION


@pytest.mark.parametrize("model_status", ["supported", "gap", "needs_confirmation"])
def test_downgraded_model_claim_does_not_retain_unsupported_high_score(model_status: str) -> None:
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "s1", "text": "负责 React 组件交付"}]}]},
        {"job_fields": {"category": "engineering", "requirements": ["熟悉 React"]}},
        ai_findings={"requirements": [{"requirement_id": "req-1", "status": model_status, "match_score": 99,
                                      "evidence_segment_keys": ["invented-segment"], "explanation": "虚构的充分依据"}]},
    )
    row = next(row for dimension in result["dimensions"] for row in dimension["requirements"])
    assert row["status"] == "partially_supported"
    assert row["match_score"] == 50.0
    assert "虚构" not in row["explanation"]


@pytest.mark.parametrize("text", ["负责 Vue 项目开发", "没有使用过 React，仅参与 Vue 项目开发", "No experience with React, delivered Vue projects"])
def test_generic_words_and_negated_skills_are_not_positive_evidence(text: str) -> None:
    resume = {"sections": [{"segments": [{"segment_key": "s1", "text": text}]}]}
    job = {"job_fields": {"category": "engineering", "requirements": ["负责 React 项目开发"]}}
    result = build_match_result(resume, job)
    assert result["ability_score"] is None
    assert result["evidence_coverage"] == 0
    # 即使模型引用了真实的否定句，也不能按“具备”计分。
    model_result = build_match_result(resume, job, ai_findings={"requirements": [{
        "requirement_id": "req-1", "status": "supported", "match_score": 100,
        "evidence_segment_keys": ["s1"],
    }]})
    if "没有" in text or "No experience" in text:
        assert model_result["ability_score"] is None


@pytest.mark.parametrize("score", ["NaN", "Infinity", "-Infinity", True, "not-a-number"])
def test_invalid_numeric_scores_do_not_crash_or_enter_results(score) -> None:
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "s1", "text": "React 组件交付"}]}]},
        {"job_fields": {"category": "engineering", "requirements": ["熟悉 React"]}},
        ai_findings={"requirements": [{"requirement_id": "req-1", "status": "partially_supported",
                                      "match_score": score, "evidence_segment_keys": ["s1"]}]},
    )
    assert result["ability_score"] == 50.0
    assert sum(item["effective_weight"] for item in result["dimensions"]) == pytest.approx(1.0)
    assert compare_salary(_salary(20_000, 30_000), _salary(20_000, 30_000, min=score))["status"] == "unknown"


def test_equivalent_legacy_tax_labels_are_comparable_without_currency_conversion() -> None:
    assert compare_salary(_salary(20_000, 30_000, tax_basis="gross"), _salary(20_000, 30_000, tax_basis="pre_tax"))["status"] == "matched"


def test_alternative_preferences_prioritise_satisfying_required_conditions() -> None:
    merged = merge_preference_contents([
        {
            "job_title": {"status": "specified", "value": "前端", "strength": "prefer"},
            "locations": {"status": "specified", "values": ["北京"], "strength": "required"},
            "work_mode": {"status": "specified", "value": "onsite", "strength": "prefer"},
            "salary": _salary(20_000, 30_000),
        },
        {
            "job_title": {"status": "unknown", "strength": "prefer"},
            "locations": {"status": "specified", "values": ["上海"], "strength": "required"},
            "work_mode": {"status": "unknown", "strength": "prefer"},
            "salary": {"status": "unknown"},
        },
    ])
    result = compare_conditions(merged, {"title": "前端工程师", "locations": ["上海"], "work_mode": "onsite", "salary": _salary(20_000, 30_000)})
    assert {row["selected_profile_no"] for row in result} == {2}
    assert not any(row["required_conflict"] for row in result)


def test_keyword_punctuation_does_not_hide_valid_skill_evidence() -> None:
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "s1", "text": "Delivered services using Python."}]}]},
        {"job_fields": {"category": "engineering", "requirements": ["Python"]}},
    )
    assert result["ability_score"] is not None


def test_java_and_javascript_are_not_treated_as_same_job_direction() -> None:
    for expected in ("Java", "Java工程师"):
        result = compare_conditions({"job_title": {"status": "specified", "value": expected, "strength": "required"}}, {"title": "JavaScript工程师"})
        assert result[0]["status"] == "unknown"


def test_past_negative_statement_does_not_hide_later_positive_experience() -> None:
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "s1", "text": "之前没有使用过 React，之后负责 React 组件迁移并完成验证。"}]}]},
        {"job_fields": {"category": "engineering", "requirements": ["熟悉 React"]}},
    )
    assert result["ability_score"] is not None


def test_confirmed_gap_cannot_be_overridden_by_a_positive_model_claim() -> None:
    result = build_match_result(
        {"explicit_gaps": ["熟悉 React"], "sections": [{"segments": [{"segment_key": "s1", "text": "React"}]}]},
        {"job_fields": {"category": "engineering", "requirements": ["熟悉 React"]}},
        ai_findings={"requirements": [{"requirement_id": "req-1", "status": "supported", "match_score": 100, "evidence_segment_keys": ["s1"]}]},
    )
    assert result["ability_score"] == 0


def test_model_match_score_preserves_strong_transferable_evidence() -> None:
    result = build_match_result(
        {"sections": [{"segments": [{"segment_key": "work-1", "text": "主导后端架构、数据平台和高并发系统交付"}]}]},
        {
            "job_fields": {
                "title": "AI 应用架构师",
                "category": "engineering",
                "requirements": ["负责复杂 Agent 系统架构与工程化落地"],
            }
        },
        ai_findings={
            "requirements": [{
                "requirement_id": "req-1",
                "dimension_key": "technical",
                "status": "partially_supported",
                "match_score": 82,
                "evidence_segment_keys": ["work-1"],
                "explanation": "通用架构和交付能力高度可迁移，但缺少 Agent 直接案例。",
            }],
            "insights": {},
        },
    )

    requirement = next(item for dimension in result["dimensions"] for item in dimension["requirements"])
    assert requirement["match_score"] == 82.0
    assert result["ability_score"] == 82.0


def test_salary_months_are_part_of_comparable_salary_basis() -> None:
    preference = {
        "status": "specified",
        "min": "18000",
        "max": "28000",
        "currency": "CNY",
        "period": "monthly",
        "tax_basis": "gross",
        "salary_months": 13,
    }
    job = {**preference, "salary_months": 12}
    assert compare_salary(preference, job)["status"] == "unknown"


def test_near_synonym_job_title_uses_direction_and_responsibilities() -> None:
    conditions = compare_conditions(
        {"job_title": {"status": "specified", "value": "前端", "strength": "required"}},
        {"title": "Web 前端工程师", "responsibilities": ["负责 React 页面开发"]},
    )
    assert conditions[0]["status"] == "matched"


def test_job_title_matching_accepts_string_responsibilities() -> None:
    conditions = compare_conditions(
        {"job_title": {"status": "specified", "value": "数据科学", "strength": "prefer"}},
        {"title": "商业分析师", "responsibilities": ["负责用户增长数据复盘"]},
    )
    assert conditions[0]["status"] == "matched"


def test_ac18_salary_boundary_matrix() -> None:
    expected = _salary(20_000, 30_000)
    assert compare_salary(expected, _salary(15_000, 25_000))["status"] == "matched"
    assert compare_salary(expected, _salary(10_000, 18_000))["status"] == "conflicted"
    assert compare_salary(expected, _salary(35_000, 40_000))["status"] == "matched"
    assert compare_salary(expected, {"status": "negotiable"})["status"] == "unknown"
    assert compare_salary(expected, None)["status"] == "unknown"
    assert compare_salary(expected, _salary(20_000, 30_000, currency="USD"))["status"] == "unknown"


def test_ac19_multiple_locations_hard_conflict_and_unknown() -> None:
    preference = {
        "job_title": {"status": "specified", "value": "前端", "strength": "preferred"},
        "locations": {
            "status": "specified",
            "values": ["杭州", "上海"],
            "strength": "required",
        },
        "work_mode": {"status": "specified", "value": "onsite", "strength": "required"},
        "salary": _salary(20_000, 30_000, strength="preferred"),
    }
    matched = compare_conditions(
        preference,
        {
            "title": "Web 前端工程师",
            "locations": ["上海"],
            "work_mode": "onsite",
            "salary": _salary(20_000, 30_000),
        },
    )
    assert {item["condition"]: item["status"] for item in matched} == {
        "job_title": "matched",
        "location": "matched",
        "work_mode": "matched",
        "salary": "matched",
    }

    conflicted = compare_conditions(
        preference,
        {
            "title": "Web 前端工程师",
            "locations": ["北京"],
            "work_mode": "remote",
            "salary": _salary(20_000, 30_000),
        },
    )
    status = {item["condition"]: item["status"] for item in conflicted}
    assert status["location"] == "conflicted"
    assert status["work_mode"] == "conflicted"

    unknown = compare_conditions(preference, {"title": "Web 前端工程师"})
    assert any(item["status"] == "unknown" for item in unknown)
    assert not all(item["status"] == "matched" for item in unknown)


def test_multiple_preferences_are_merged_as_alternative_conditions() -> None:
    preferences = [
        {
            "job_title": {"status": "specified", "value": "前端", "strength": "required"},
            "locations": {"status": "specified", "values": ["杭州"], "strength": "required"},
            "work_mode": {"status": "unknown", "value": None, "strength": "prefer"},
            "salary": {"status": "unknown", "strength": "prefer"},
        },
        {
            "job_title": {"status": "specified", "value": "后端", "strength": "required"},
            "locations": {"status": "specified", "values": ["上海"], "strength": "required"},
            "work_mode": {"status": "unknown", "value": None, "strength": "prefer"},
            "salary": {"status": "unknown", "strength": "prefer"},
        },
    ]
    merged = merge_preference_contents(preferences)
    conditions = compare_conditions(
        merged,
        {"title": "后端工程师", "locations": ["上海"]},
    )

    assert merged["strategy"] == "any"
    assert len(merged["profiles"]) == 2
    assert {item["condition"]: item["status"] for item in conditions}["location"] == "matched"
    assert {item["selected_profile_no"] for item in conditions} == {2}
    assert not any(item["required_conflict"] for item in conditions)


def test_hard_condition_conflict_is_not_hidden_by_high_ability_score() -> None:
    result = build_match_result(
        {
            "sections": [
                {
                    "segments": [
                        {
                            "segment_key": "work-1",
                            "text": "负责 React TypeScript 前端开发、项目交付、性能测试和业务增长",
                        }
                    ]
                }
            ]
        },
        {
            "job_fields": {
                "title": "前端工程师",
                "category": "engineering",
                "requirements": [
                    "React TypeScript 前端开发",
                    "项目交付",
                    "性能测试",
                    "业务增长",
                ],
                "locations": ["上海"],
                "work_mode": "onsite",
                "salary": _salary(20_000, 30_000),
            }
        },
        {
            "job_title": {"status": "specified", "value": "前端", "strength": "preferred"},
            "locations": {"status": "specified", "values": ["杭州"], "strength": "required"},
            "work_mode": {"status": "specified", "value": "onsite", "strength": "preferred"},
            "salary": _salary(20_000, 30_000, strength="preferred"),
        },
    )
    assert result["ability_score"] is not None
    assert result["overall_advice"]["status"] == "not_priority"
