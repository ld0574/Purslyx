"""面试练习的稳定评价维度与聚合规则。"""

from __future__ import annotations

from typing import Any

RUBRIC_VERSION = "interview-rubric-v1"
FEEDBACK_SCHEMA_VERSION = "interview-feedback-v4"
SUMMARY_SCHEMA_VERSION = "interview-summary-v2"

DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("relevance", "回答切题度"),
    ("specificity", "事实具体度"),
    ("ownership", "个人贡献清晰度"),
    ("outcome_evidence", "结果证据力度"),
    ("communication", "表达结构与清晰度"),
)
LEVEL_SCORES = {"strong": 100, "partial": 50, "missing": 0}


def _level(value: Any) -> str:
    level = str(value or "missing").strip().lower()
    return level if level in LEVEL_SCORES else "missing"


def normalise_dimensions(value: Any, *, answer: str = "") -> dict[str, dict[str, Any]]:
    """规范模型维度；证据必须是回答原文的精确片段。"""

    raw = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for key, label in DIMENSIONS:
        item = raw.get(key) if isinstance(raw.get(key), dict) else {}
        level = _level(item.get("status"))
        quote = str(item.get("evidence_quote") or "").strip()[:300]
        if not quote or quote not in answer:
            quote = ""
        result[key] = {
            "label": label,
            "status": level,
            "feedback": str(item.get("feedback") or "尚未提供足够信息。").strip()[:500],
            "evidence_quote": quote or None,
        }
    return result


def local_dimensions(answer: str) -> dict[str, dict[str, Any]]:
    """本地降级评价只使用回答中可观察到的信号，不推断经历真假。"""

    clean = answer.strip()
    snippet = clean[:120] or None
    if len(clean) < 12:
        levels = {key: "missing" for key, _ in DIMENSIONS}
    else:
        has_ownership = any(token in clean for token in ("我", "本人", "负责", "主导", "决定", "设计", "推动"))
        has_outcome = any(token in clean for token in ("结果", "最终", "提升", "降低", "完成", "上线", "%", "验证")) or any(char.isdigit() for char in clean)
        levels = {
            "relevance": "partial",
            "specificity": "strong" if len(clean) >= 100 else "partial",
            "ownership": "partial" if has_ownership else "missing",
            "outcome_evidence": "partial" if has_outcome else "missing",
            "communication": "strong" if len(clean) >= 100 else "partial",
        }
    feedbacks = {
        "relevance": "回答已经触及问题主题；继续明确它与岗位要求的直接关系。",
        "specificity": "补充具体场景、约束和关键细节，避免只列概念。",
        "ownership": "说清你本人负责的范围、判断和实际行动。",
        "outcome_evidence": "补充结果、指标或能够验证变化的事实。",
        "communication": "按背景、目标、行动、结果组织重点，减少跳跃。",
    }
    return {
        key: {
            "label": label,
            "status": levels[key],
            "feedback": feedbacks[key],
            "evidence_quote": snippet if levels[key] != "missing" else None,
        }
        for key, label in DIMENSIONS
    }


def attach_feedback_metadata(content: dict[str, Any], answer: str) -> dict[str, Any]:
    result = dict(content)
    result["evaluation_dimensions"] = normalise_dimensions(result.get("evaluation_dimensions"), answer=answer)
    result["rubric_version"] = RUBRIC_VERSION
    result["schema_version"] = FEEDBACK_SCHEMA_VERSION
    return result


def aggregate_main_feedback(contents: list[dict[str, Any]], completion_type: str) -> dict[str, Any]:
    """只对三道完整主问题计算练习指数；追问和旧版反馈不会参与。"""

    base = {
        "rubric_version": RUBRIC_VERSION,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "practice_index": None,
        "evaluation_dimensions": None,
    }
    valid = [
        item for item in contents
        if isinstance(item, dict)
        and item.get("rubric_version") == RUBRIC_VERSION
        and isinstance(item.get("evaluation_dimensions"), dict)
    ]
    if completion_type != "full" or len(valid) != 3:
        return base

    dimensions: dict[str, dict[str, Any]] = {}
    for key, label in DIMENSIONS:
        scores = [LEVEL_SCORES[_level(item["evaluation_dimensions"].get(key, {}).get("status"))] for item in valid]
        score = round(sum(scores) / len(scores))
        status = "strong" if score >= 75 else "partial" if score >= 40 else "missing"
        dimensions[key] = {"label": label, "status": status, "score": score}
    base["evaluation_dimensions"] = dimensions
    base["practice_index"] = round(sum(item["score"] for item in dimensions.values()) / len(dimensions))
    return base


def enrich_summary(content: dict[str, Any], main_feedback: list[dict[str, Any]], completion_type: str) -> dict[str, Any]:
    return {**content, **aggregate_main_feedback(main_feedback, completion_type)}
