"""可复核的岗位匹配内核。

模型只负责把 JD 与简历整理成结构化候选结果；最终能力分、覆盖率和条件状态全部由
这里的代码计算，防止模型随机输出一个无法解释的综合分。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

DIMENSION_CONFIG = {
    "engineering": [
        ("technical", "技术能力", 0.40),
        ("delivery", "工程交付", 0.30),
        ("quality", "问题解决与质量", 0.20),
        ("business", "业务理解", 0.10),
    ],
    "product": [
        ("discovery", "需求洞察", 0.30),
        ("delivery", "产品方案与交付", 0.35),
        ("data", "数据分析与验证", 0.25),
        ("business", "业务理解", 0.10),
    ],
    "operations": [
        ("strategy", "策略与执行", 0.35),
        ("growth", "内容渠道与用户运营", 0.30),
        ("data", "数据复盘", 0.25),
        ("business", "业务理解", 0.10),
    ],
    "general": [
        ("core", "核心能力", 0.40),
        ("delivery", "职责交付", 0.30),
        ("problem_solving", "问题解决与协作", 0.20),
        ("business", "业务理解", 0.10),
    ],
}


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.-]*|[\u4e00-\u9fff]{2,6}", text.lower()))
    # 中文短语再拆成二字词，保证“性能优化”能和“性能”有可解释的交集。
    for phrase in list(words):
        if re.fullmatch(r"[\u4e00-\u9fff]+", phrase):
            words.update(phrase[index : index + 2] for index in range(len(phrase) - 1))
    return {word for word in words if len(word) >= 2 or re.search(r"[a-z0-9]", word)}


def _resume_segments(resume_content: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        segment
        for section in resume_content.get("sections", [])
        for segment in section.get("segments", [])
        if isinstance(segment, dict) and segment.get("text")
    ]


def _requirements(job_fields: dict[str, Any]) -> list[dict[str, Any]]:
    raw = job_fields.get("requirements") or job_fields.get("responsibilities") or []
    result = []
    if isinstance(raw, str):
        raw = re.split(r"[\n；;。]", raw)
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str) and item.strip():
            result.append({"requirement_id": f"req-{index}", "text": item.strip()})
        elif isinstance(item, dict) and item.get("text"):
            result.append({"requirement_id": item.get("requirement_id", f"req-{index}"), **item})
    return result


def _dimension_for(text: str, category: str) -> str:
    value = text.lower()
    keywords = {
        "technical": ("开发", "前端", "后端", "react", "vue", "python", "java", "技术", "sql"),
        "delivery": ("交付", "项目", "负责", "方案", "上线", "协作"),
        "quality": ("性能", "质量", "测试", "问题", "稳定", "排查"),
        "business": ("业务", "用户", "行业", "增长", "商业"),
        "discovery": ("需求", "研究", "洞察", "用户", "场景"),
        "data": ("数据", "指标", "分析", "复盘", "实验"),
        "strategy": ("策略", "规划", "执行", "渠道"),
        "growth": ("内容", "运营", "增长", "用户", "社群"),
        "problem_solving": ("问题", "解决", "协作", "沟通"),
        "core": ("能力", "经验", "专业"),
    }
    for dimension, words in keywords.items():
        if any(word in value for word in words):
            if dimension in {key for key, _, _ in DIMENSION_CONFIG.get(category, DIMENSION_CONFIG["general"])}:
                return dimension
    return DIMENSION_CONFIG.get(category, DIMENSION_CONFIG["general"])[0][0]


def _classify(requirement: str, segments: list[dict[str, Any]], explicit_gaps: set[str]) -> tuple[str, list[dict[str, Any]]]:
    req_tokens = _tokens(requirement)
    evidence: list[dict[str, Any]] = []
    for segment in segments:
        segment_text = str(segment.get("text", ""))
        hits = sorted(req_tokens & _tokens(segment_text))
        if hits:
            evidence.append(
                {
                    "segment_key": segment.get("segment_key"),
                    "quote": segment_text,
                    "matched_terms": hits[:8],
                    "source_type": segment.get("source", "user_confirmed"),
                }
            )
    if requirement in explicit_gaps:
        return "gap", evidence
    if not evidence:
        return "needs_confirmation", []
    hit_count = sum(len(item["matched_terms"]) for item in evidence)
    status = "supported" if hit_count >= max(2, len(req_tokens) // 2) else "partially_supported"
    return status, evidence[:3]


def _model_classification(
    requirement: str,
    finding: dict[str, Any],
    segments: list[dict[str, Any]],
    explicit_gaps: set[str],
) -> tuple[str, list[dict[str, Any]], str] | None:
    """校验模型判断后再把它交给评分内核。

    模型只能引用已经存在的简历段落。引用无效、声称具备能力但没有证据，或把
    “没有找到证据”直接写成明确差距时，统一降级为待确认，避免模型输出覆盖事实。
    """

    if not isinstance(finding, dict):
        return None
    allowed_statuses = {"supported", "partially_supported", "gap", "needs_confirmation"}
    status = str(finding.get("status", "needs_confirmation"))
    if status not in allowed_statuses:
        status = "needs_confirmation"
    segment_by_key = {str(item.get("segment_key")): item for item in segments}
    evidence: list[dict[str, Any]] = []
    raw_keys = finding.get("evidence_segment_keys") or []
    if isinstance(raw_keys, list):
        for key in raw_keys:
            segment = segment_by_key.get(str(key))
            if segment is None:
                continue
            evidence.append(
                {
                    "segment_key": segment.get("segment_key"),
                    "quote": str(segment.get("text", "")),
                    "matched_terms": sorted(_tokens(requirement) & _tokens(str(segment.get("text", ""))))[:8],
                    "source_type": segment.get("source", "user_confirmed"),
                }
            )
    if status in {"supported", "partially_supported"} and not evidence:
        status = "needs_confirmation"
    if status == "gap" and requirement not in explicit_gaps:
        status = "needs_confirmation"
    fallback_explanation = ""
    if status == "needs_confirmation" and not evidence:
        # 模型有时会漏填引用键，但要求文本和简历原文仍然存在可核对词项。
        # 只把这种兜底结果降为“部分支持”，不直接提升为完全支持，避免漏引用把整份报告算成 0 分。
        fallback_status, fallback_evidence = _classify(requirement, segments, explicit_gaps)
        if fallback_status in {"supported", "partially_supported"} and fallback_evidence:
            status = "partially_supported"
            evidence = fallback_evidence
            fallback_explanation = "模型未提交可用引用；服务端在确认资料中找到部分可核对依据，仍需补充完整经历。"
    explanation = str(finding.get("explanation") or "").strip() or fallback_explanation
    if not explanation:
        explanation = {
            "supported": "模型识别到已有确认经历提供了对应依据。",
            "partially_supported": "模型识别到部分可迁移依据，还需要补充承担范围或结果。",
            "gap": "确认资料明确显示该要求存在差距。",
            "needs_confirmation": "当前资料没有足够依据，不能断言具备或不具备。",
        }[status]
    return status, evidence[:3], explanation[:500]


def _number(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _field_status(obj: dict[str, Any] | None) -> str:
    if not obj:
        return "unknown"
    return str(obj.get("status", "unknown"))


def merge_preference_contents(preferences: list[dict[str, Any]]) -> dict[str, Any]:
    """把多条岗位期望封装成一次模型输入；每条期望仍是独立的备选条件组合。"""

    return {
        "strategy": "any",
        "profiles": [
            {"profile_no": index, "conditions": preference}
            for index, preference in enumerate(preferences, start=1)
            if isinstance(preference, dict)
        ],
    }


def _preference_profiles(preference_content: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(preference_content, dict) or not preference_content:
        return [{}]
    raw_profiles = preference_content.get("profiles")
    if not isinstance(raw_profiles, list):
        return [preference_content]
    profiles = []
    for raw_profile in raw_profiles:
        if not isinstance(raw_profile, dict):
            continue
        content = raw_profile.get("conditions", raw_profile)
        if isinstance(content, dict):
            profiles.append(content)
    return profiles or [{}]


def compare_salary(preference: dict[str, Any] | None, job_salary: dict[str, Any] | None) -> dict[str, Any]:
    """按文档规则比较薪资，不把未知当成冲突。"""

    preference_status = _field_status(preference)
    if preference_status == "unrestricted":
        return {"status": "matched", "explanation": "求职期望明确表示不限制薪资。"}
    if not preference or preference_status != "specified":
        return {"status": "unknown", "explanation": "求职期望未明确提供薪资范围。"}
    if not job_salary or _field_status(job_salary) != "specified":
        if job_salary and _field_status(job_salary) == "negotiable":
            return {"status": "unknown", "explanation": "岗位薪资为面议，暂不可与期望范围比较。"}
        return {"status": "unknown", "explanation": "岗位未披露可比较的薪资范围。"}
    fields = ("currency", "period", "tax_basis", "salary_months")
    if any(not preference.get(field) or not job_salary.get(field) for field in fields):
        return {"status": "unknown", "explanation": "币种、周期或税前税后口径缺失，暂不可比较。"}
    if any(preference.get(field) != job_salary.get(field) for field in fields):
        return {"status": "unknown", "explanation": "双方薪资口径不同，未擅自换算。"}
    expected_min = _number(preference.get("min"))
    expected_max = _number(preference.get("max"))
    job_min = _number(job_salary.get("min"))
    job_max = _number(job_salary.get("max"))
    if None in (expected_min, expected_max, job_min, job_max) or expected_min > expected_max or job_min > job_max:
        return {"status": "unknown", "explanation": "薪资区间缺失或无效，需要先修正。"}
    if job_max < expected_min:
        return {"status": "conflicted", "explanation": "岗位披露上限低于求职期望下限。"}
    if job_min > expected_max:
        return {"status": "matched", "explanation": "岗位披露范围高于期望上限，不因此判定为不匹配。"}
    return {"status": "matched", "explanation": "岗位披露范围与期望有交集，仍需确认实际预算。"}


def _compare_conditions_single(preference: dict[str, Any], job_fields: dict[str, Any]) -> list[dict[str, Any]]:
    """计算一条岗位期望的岗位方向、地点、办公方式和薪资条件。"""

    results: list[dict[str, Any]] = []
    title = preference.get("job_title") or {}
    job_title = job_fields.get("title") or job_fields.get("job_title")
    if title.get("status") == "unrestricted":
        results.append({"condition": "job_title", "status": "matched", "preference_value": title, "strength": title.get("strength"), "required_conflict": False, "explanation": "求职期望明确表示不限制岗位方向。"})
    elif title.get("status") != "specified" or not job_title:
        results.append({"condition": "job_title", "status": "unknown", "preference_value": title, "strength": title.get("strength"), "required_conflict": False, "explanation": "岗位方向信息不足。"})
    elif _job_title_matches(str(title.get("value", "")), str(job_title), job_fields):
        results.append({"condition": "job_title", "status": "matched", "preference_value": title, "strength": title.get("strength"), "required_conflict": False, "explanation": "岗位名称方向相符。"})
    else:
        results.append({"condition": "job_title", "status": "unknown", "preference_value": title, "strength": title.get("strength"), "required_conflict": False, "explanation": "名称不同，需要结合实际职责确认方向。"})

    locations = preference.get("locations") or {}
    wanted = {str(item).strip() for item in locations.get("values", []) if str(item).strip()}
    raw_actual = job_fields.get("locations") or []
    if isinstance(raw_actual, str):
        raw_actual = re.split(r"[/／、,，|]", raw_actual)
    actual = {str(item).strip() for item in raw_actual if str(item).strip()}
    if locations.get("status") == "unrestricted":
        results.append({"condition": "location", "status": "matched", "preference_value": locations, "strength": locations.get("strength"), "required_conflict": False, "explanation": "求职期望明确表示不限制地点。"})
    elif locations.get("status") != "specified" or not actual:
        results.append({"condition": "location", "status": "unknown", "preference_value": locations, "strength": locations.get("strength"), "required_conflict": False, "explanation": "地点未完整披露或未提供期望。"})
    elif wanted & actual:
        results.append({"condition": "location", "status": "matched", "preference_value": locations, "strength": locations.get("strength"), "required_conflict": False, "explanation": "岗位地点包含可接受城市。"})
    else:
        results.append({"condition": "location", "status": "conflicted", "preference_value": locations, "strength": locations.get("strength"), "required_conflict": locations.get("strength") == "required", "explanation": "岗位地点不在已确认的可接受城市内。"})

    work_mode = preference.get("work_mode") or {}
    actual_mode = job_fields.get("work_mode")
    if work_mode.get("status") == "unrestricted":
        results.append({"condition": "work_mode", "status": "matched", "preference_value": work_mode, "strength": work_mode.get("strength"), "required_conflict": False, "explanation": "求职期望明确表示不限制办公方式。"})
    elif work_mode.get("status") != "specified" or not actual_mode:
        results.append({"condition": "work_mode", "status": "unknown", "preference_value": work_mode, "strength": work_mode.get("strength"), "required_conflict": False, "explanation": "办公方式未完整披露或未提供期望。"})
    elif work_mode.get("value") == actual_mode:
        results.append({"condition": "work_mode", "status": "matched", "preference_value": work_mode, "strength": work_mode.get("strength"), "required_conflict": False, "explanation": "办公方式符合已确认期望。"})
    else:
        results.append({"condition": "work_mode", "status": "conflicted", "preference_value": work_mode, "strength": work_mode.get("strength"), "required_conflict": work_mode.get("strength") == "required", "explanation": "办公方式与已确认期望不同。"})

    salary = preference.get("salary")
    results.append(
        {
            "condition": "salary",
            **compare_salary(salary, job_fields.get("salary")),
            "preference_value": salary,
            "strength": salary.get("strength") if isinstance(salary, dict) else None,
            "required_conflict": False,
        }
    )
    results[-1]["required_conflict"] = results[-1]["status"] == "conflicted" and results[-1]["strength"] == "required"
    return results


def compare_conditions(preference: dict[str, Any] | None, job_fields: dict[str, Any]) -> list[dict[str, Any]]:
    """在一次计算中比较多套期望，并选择整体最合适的一套。"""

    profiles = _preference_profiles(preference)
    if len(profiles) == 1:
        return _compare_conditions_single(profiles[0], job_fields)

    profile_rows = [_compare_conditions_single(profile, job_fields) for profile in profiles]
    status_weight = {"matched": 3, "unknown": 1, "conflicted": 0}

    def profile_rank(rows: list[dict[str, Any]]) -> tuple[int, int, int, int]:
        statuses = [str(row.get("status")) for row in rows]
        return (
            sum(status_weight.get(status, 0) for status in statuses),
            statuses.count("matched"),
            -sum(bool(row.get("required_conflict")) for row in rows),
            -statuses.count("unknown"),
        )

    selected_index, selected_rows = max(enumerate(profile_rows), key=lambda item: profile_rank(item[1]))
    return [
        {
            **row,
            "selected_profile_no": selected_index + 1,
            "preference_count": len(profiles),
            "explanation": f"按第 {selected_index + 1} 条岗位期望：{row['explanation']}",
        }
        for row in selected_rows
    ]


def _job_title_matches(expected: str, actual: str, job_fields: dict[str, Any]) -> bool:
    """用岗位方向和职责共同判断近义岗位，避免只依赖字符串包含。"""

    expected_value = expected.strip().lower()
    actual_value = actual.strip().lower()
    if not expected_value or not actual_value:
        return False
    if expected_value in actual_value or actual_value in expected_value:
        return True
    aliases = {
        "前端": {"前端开发", "web前端", "前端工程师", "frontend", "frontend engineer"},
        "后端": {"后端开发", "后端工程师", "backend", "backend engineer"},
        "产品": {"产品经理", "产品负责人", "product manager", "pm"},
        "运营": {"运营经理", "用户运营", "内容运营", "增长运营"},
    }
    for family, names in aliases.items():
        expected_in_family = family in expected_value or any(name in expected_value for name in names)
        actual_in_family = family in actual_value or any(name in actual_value for name in names)
        if expected_in_family and actual_in_family:
            return True
    expected_tokens = _tokens(expected_value)
    actual_tokens = _tokens(actual_value)
    if expected_tokens and actual_tokens and expected_tokens & actual_tokens:
        return True
    responsibilities = job_fields.get("responsibilities") or []
    if isinstance(responsibilities, str):
        responsibilities = [responsibilities]
    responsibility_text = " ".join(
        item
        if isinstance(item, str)
        else str(item.get("text", ""))
        if isinstance(item, dict)
        else str(item)
        for item in responsibilities
    )
    return bool(expected_tokens & _tokens(responsibility_text))


def _verification_items(
    dimensions: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把证据缺口和未知条件整理成可执行的核实清单。"""

    items: list[dict[str, Any]] = []
    for dimension in dimensions:
        for requirement in dimension.get("requirements", []):
            finding = str(requirement.get("status", "needs_confirmation"))
            if finding not in {"gap", "needs_confirmation"}:
                continue
            requirement_id = str(requirement.get("requirement_id", "requirement"))
            quote = str(requirement.get("job_quote", "")).strip()
            items.append(
                {
                    "id": f"requirement:{requirement_id}",
                    "kind": "requirement",
                    "status": finding,
                    "dimension_key": dimension.get("key"),
                    "requirement_id": requirement_id,
                    "job_quote": quote,
                    "reason": requirement.get("explanation", "当前资料不足以确认该要求。"),
                    "question": f"请结合真实经历，具体说明你在“{quote[:160]}”中的背景、个人行动和可核验结果。",
                }
            )

    condition_labels = {
        "job_title": "岗位方向",
        "location": "工作地点",
        "work_mode": "办公方式",
        "salary": "薪资口径",
    }
    for condition in conditions:
        finding = str(condition.get("status", "unknown"))
        if finding not in {"unknown", "conflicted"}:
            continue
        code = str(condition.get("condition", "condition"))
        label = condition_labels.get(code, code)
        items.append(
            {
                "id": f"condition:{code}",
                "kind": "condition",
                "status": finding,
                "condition": code,
                "label": label,
                "reason": condition.get("explanation", "条件信息需要进一步确认。"),
                "question": f"请确认“{label}”的实际情况，以及它是否满足本次岗位判断所需的口径。",
            }
        )
    return items[:20]


def _interview_questions(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按缺口优先生成招聘方可直接使用的结构化问题。"""

    priority = {"gap": 0, "needs_confirmation": 1, "partially_supported": 2, "supported": 3}
    rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    sequence = 0
    for dimension in dimensions:
        for requirement in dimension.get("requirements", []):
            sequence += 1
            rows.append((priority.get(str(requirement.get("status")), 4), sequence, dimension, requirement))
    rows.sort(key=lambda item: (item[0], item[1]))
    result: list[dict[str, Any]] = []
    for index, (_, _, dimension, requirement) in enumerate(rows[:6], start=1):
        requirement_id = str(requirement.get("requirement_id", f"requirement-{index}"))
        quote = str(requirement.get("job_quote", "")).strip()
        finding = str(requirement.get("status", "needs_confirmation"))
        result.append(
            {
                "id": f"interview:{requirement_id}",
                "question_type": "requirement_verification",
                "priority": "high" if finding in {"gap", "needs_confirmation"} else "normal",
                "question_text": f"请举例说明你如何完成“{quote[:160]}”，其中你本人负责什么，最后结果如何核验？",
                "basis": {
                    "requirement_id": requirement_id,
                    "dimension_key": dimension.get("key"),
                    "finding_type": finding,
                    "evidence_segment_keys": [item.get("segment_key") for item in requirement.get("evidence", []) if item.get("segment_key")],
                    "method": "STAR",
                    "rule_version": "interview-question-basis-v1",
                },
            }
        )
    return result


def build_match_result(
    resume_content: dict[str, Any],
    job_content: dict[str, Any],
    preference_content: dict[str, Any] | None = None,
    context_type: str = "seeker_pool",
    ai_findings: dict[str, Any] | None = None,
    prompt_version: str = "analysis-local-v1",
) -> dict[str, Any]:
    """生成可持久化、可复核的匹配报告。"""

    job_fields = job_content.get("job_fields") or {}
    category = job_fields.get("category") or "general"
    dimensions = DIMENSION_CONFIG.get(category, DIMENSION_CONFIG["general"])
    segments = _resume_segments(resume_content)
    explicit_gaps = set(resume_content.get("explicit_gaps", []))
    requirements = _requirements(job_fields)
    model_requirements = {
        str(item.get("requirement_id")): item
        for item in (ai_findings or {}).get("requirements", [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    dimension_rows: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in dimensions}
    for requirement in requirements:
        model_finding = model_requirements.get(str(requirement["requirement_id"]))
        model_dimension = str(model_finding.get("dimension_key", "")) if model_finding else ""
        dimension = model_dimension or requirement.get("dimension") or _dimension_for(requirement["text"], category)
        if dimension not in dimension_rows:
            dimension = dimensions[0][0]
        validated_model = _model_classification(requirement["text"], model_finding, segments, explicit_gaps) if model_finding else None
        if validated_model is None:
            status, evidence = _classify(requirement["text"], segments, explicit_gaps)
            explanation = {
                "supported": "已有确认经历提供了对应依据。",
                "partially_supported": "已有经历提供了部分可迁移依据，还需补充承担范围或结果。",
                "gap": "确认资料明确显示该要求存在差距。",
                "needs_confirmation": "当前资料没有足够依据，不能断言具备或不具备。",
            }[status]
        else:
            status, evidence, explanation = validated_model
        dimension_rows[dimension].append(
            {
                "requirement_id": requirement["requirement_id"],
                "dimension_key": dimension,
                "job_quote": requirement["text"],
                "status": status,
                "evidence": evidence,
                "explanation": explanation,
            }
        )

    total_score = Decimal("0")
    total_coverage = Decimal("0")
    applicable_weight = Decimal("0")
    dimension_output = []
    for key, label, base_weight in dimensions:
        rows = dimension_rows[key]
        if not rows:
            dimension_output.append(
                {"key": key, "label": label, "base_weight": base_weight, "effective_weight": 0, "score": None, "evidence_status": "not_applicable", "requirements": []}
            )
            continue
        weight = Decimal(str(base_weight))
        applicable_weight += weight
        score_sum = Decimal("0")
        coverage_sum = Decimal("0")
        for row in rows:
            if row["status"] == "supported":
                score_sum += Decimal("1")
                coverage_sum += Decimal("1")
            elif row["status"] == "partially_supported":
                score_sum += Decimal("0.5")
                coverage_sum += Decimal("1")
            elif row["status"] == "gap":
                coverage_sum += Decimal("1")
        row_score = (score_sum / Decimal(len(rows)) * Decimal("100")).quantize(Decimal("0.1"))
        row_coverage = (coverage_sum / Decimal(len(rows)) * Decimal("100")).quantize(Decimal("0.1"))
        dimension_output.append(
            {
                "key": key,
                "label": label,
                "base_weight": float(weight),
                "effective_weight": float(weight),
                "score": float(row_score),
                "evidence_status": "needs_confirmation" if row_coverage < 100 else "available",
                "summary": f"{row_score} 分，证据覆盖率 {row_coverage}%",
                "requirements": rows,
            }
        )
        total_score += weight * score_sum / Decimal(len(rows))
        total_coverage += weight * coverage_sum / Decimal(len(rows))

    if applicable_weight == 0 or not requirements:
        ability_score = None
        coverage = None
    else:
        coverage = (total_coverage / applicable_weight).quantize(Decimal("0.0001"))
        # needs_confirmation 不是“不具备能力”。覆盖率为零时没有可复核的评分依据，
        # 不能把未知项的零贡献伪装成 0 分；页面应显示待补充并引导用户补事实。
        ability_score = (
            None
            if total_coverage == 0
            else (total_score / applicable_weight * Decimal("100")).quantize(Decimal("0.1"))
        )

    conditions = compare_conditions(preference_content, job_fields)
    verification_items = _verification_items(dimension_output, conditions)
    interview_questions = _interview_questions(dimension_output)
    hard_conflict = any(item.get("required_conflict") for item in conditions)
    unknown = any(item["status"] == "unknown" for item in conditions)
    if hard_conflict:
        advice = {"status": "not_priority", "text": "当前不优先，先确认或调整必须符合的条件。", "next_steps": ["先处理硬性条件冲突，再决定是否继续准备。"]}
    elif ability_score is None or (coverage is not None and coverage < Decimal("0.5")):
        advice = {"status": "needs_more_information", "text": "当前资料不足，先补充可核验经历和岗位条件。", "next_steps": ["补充与岗位要求直接相关的事实。"]}
    elif unknown:
        advice = {"status": "needs_confirmation", "text": "能力分析已有依据，但部分求职条件仍待确认。", "next_steps": ["核实岗位地点、办公方式和薪资口径。"]}
    else:
        advice = {"status": "review", "text": "请结合能力依据和条件对照做人工判断。", "next_steps": ["查看证据并决定是否补充事实或开始面试练习。"]}

    return {
        "context_type": context_type,
        "job_category": category,
        "ability_score": float(ability_score) if ability_score is not None else None,
        "evidence_coverage": float(coverage) if coverage is not None else None,
        "dimensions": dimension_output,
        "conditions": conditions,
        "verification_items": verification_items,
        "interview_questions": interview_questions,
        "overall_advice": advice,
        "scoring_rule_version": "ability-v0.2",
        "result_schema_version": "analysis-result-v1",
        "prompt_version": prompt_version,
        "ai_insights": {
            **(ai_findings.get("insights", {}) if ai_findings else {}),
            **(
                {
                    "model_score": ai_findings["model_score"],
                    "model_score_rationale": ai_findings.get("model_score_rationale", ""),
                }
                if ai_findings and ai_findings.get("model_score") is not None
                else {}
            ),
        },
    }
