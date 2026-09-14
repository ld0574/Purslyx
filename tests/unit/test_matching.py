from server.app.matching import build_match_result, compare_conditions, compare_salary


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
