"""从已校验的匹配报告生成个人投递排序，不调用模型、不表示录用概率。"""

from __future__ import annotations

import math
from typing import Any

RECOMMENDATION_VERSION = "application-recommendation-v1"
TIER_LABELS = {"priority": "优先投", "consider": "可以尝试", "verify": "先核实", "defer": "暂缓投"}


def _number(value: Any, lower: float, upper: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and lower <= number <= upper else None


def application_recommendation(report: dict[str, Any]) -> dict[str, Any]:
    score = _number(report.get("ability_score"), 0, 100)
    coverage = _number(report.get("evidence_coverage"), 0, 1)
    conditions = [row for row in report.get("conditions", []) if isinstance(row, dict)]
    hard_conflicts = [
        row
        for row in conditions
        if row.get("required_conflict")
        or (row.get("strength") == "required" and row.get("status") == "conflicted")
    ]
    required_unknowns = [
        row
        for row in conditions
        if row.get("strength") == "required" and row.get("status") == "unknown"
    ]
    if hard_conflicts:
        tier, reason = "defer", "存在必须满足的条件冲突，当前不建议优先投入投递精力。"
    elif required_unknowns:
        tier, reason = "verify", "必须满足的条件还不明确，请先核实再决定是否投递。"
    elif score is None or coverage is None or coverage < 0.6:
        tier, reason = "verify", "可核验依据不足，暂不能可靠判断是否值得投递。"
    elif score >= 75 and coverage >= 0.7:
        tier, reason = "priority", "已确认经历与岗位较贴合，且没有已知的硬性条件冲突。"
    elif score >= 55:
        tier, reason = "consider", "已有相关或可迁移依据，可以尝试，但需要针对薄弱项准备。"
    else:
        tier, reason = "defer", "当前可核验匹配依据较弱，建议先把精力放在更贴合的岗位。"

    strengths: list[dict[str, Any]] = []
    risks: list[str] = []
    for condition in conditions:
        if condition.get("status") in {"unknown", "conflicted"}:
            text = str(condition.get("explanation") or "岗位条件需核实")
            if text not in risks:
                risks.append(text[:240])
    for dimension in report.get("dimensions", []):
        if not isinstance(dimension, dict):
            continue
        for row in dimension.get("requirements", []):
            if not isinstance(row, dict):
                continue
            evidence = [
                item
                for item in row.get("evidence", [])
                if isinstance(item, dict) and item.get("quote") and item.get("segment_key")
            ]
            if row.get("status") in {"supported", "partially_supported"} and evidence:
                strengths.append(
                    {
                        "requirement": str(row.get("job_quote") or "")[:200],
                        "explanation": str(row.get("explanation") or "已有确认经历作为依据")[:240],
                        "evidence": str(evidence[0]["quote"])[:200],
                        "segment_key": evidence[0]["segment_key"],
                    }
                )
            elif row.get("status") == "gap":
                risks.append(f"明确差距：{str(row.get('job_quote') or '')[:180]}")
    verification = [
        str(row.get("question") or row.get("reason") or "")[:240]
        for row in report.get("verification_items", [])
        if isinstance(row, dict)
    ]
    if coverage is not None and coverage < 1:
        risks.append("部分岗位要求尚无充分依据，未知不代表你不具备能力。")
    return {
        "version": RECOMMENDATION_VERSION,
        "tier": tier,
        "label": TIER_LABELS[tier],
        "reason": reason,
        "ability_score": score,
        "evidence_coverage": coverage,
        "strengths": strengths[:2],
        "risks": list(dict.fromkeys(risks))[:3],
        "verification_items": [item for item in dict.fromkeys(verification) if item][:3],
        "disclaimer": "仅用于个人投递排序，不代表招聘结果；去投递由你在原平台确认。",
    }
