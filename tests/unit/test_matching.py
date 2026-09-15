from server.app.matching import build_match_result, compare_conditions, compare_salary


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
