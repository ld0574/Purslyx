from server.app.matching import build_match_result, compare_salary


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
