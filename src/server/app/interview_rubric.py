"""面试练习的稳定评价维度与聚合规则。"""

from __future__ import annotations

import logging
import re
from typing import Any

from .matching import _evidence_tokens
from .request_context import current_request_id

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
PRACTICE_SUGGESTIONS = {
    "relevance": "先用一句话直接回答问题，再选一段与目标岗位能力对应的经历，说明它为什么相关。",
    "specificity": "选一段真实经历，补充发生的场景、规模、约束和关键细节，避免只列概念。",
    "ownership": "明确区分团队成果与你本人负责的范围，重点讲一个由你作出的判断和具体行动。",
    "outcome_evidence": "补充结果与核验方式；有记录时引用指标，没有数字时说明可观察的变化和复盘。",
    "communication": "先给结论，再按背景、目标、行动、结果组织回答，每部分保留一个重点。",
}


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
        raw_quote = item.get("evidence_quote")
        quote = raw_quote.strip() if isinstance(raw_quote, str) else ""
        feedback = str(item.get("feedback") or "尚未提供足够信息。").strip()[:500]
        evidence_status = "verified" if quote and quote in answer and level != "missing" else "not_provided"
        if item.get("evidence_status") == "unverified" or not isinstance(item.get("status"), str) or item["status"].strip().lower() not in LEVEL_SCORES:
            logging.getLogger(__name__).warning(
                "interview dimension invalid request_id=%s rubric_version=%s stage=dimension_validation dimension=%s error_type=InvalidDimension",
                current_request_id() or "unknown", RUBRIC_VERSION, key,
            )
            evidence_status = "unverified"
            level = "missing"
            quote = ""
            feedback = "该项评价字段不完整或证据未通过校验，暂时无法判断该维度。"
        if level != "missing" and (not quote or quote not in answer):
            logging.getLogger(__name__).warning(
                "interview evidence rejected request_id=%s rubric_version=%s stage=dimension_validation dimension=%s error_type=UnmatchedEvidence",
                current_request_id() or "unknown", RUBRIC_VERSION, key,
            )
            level = "missing"
            evidence_status = "unverified"
            feedback = "该项评价未提供可核对的回答原文依据，暂时无法判断该维度。"
        if level == "missing":
            quote = ""
        result[key] = {
            "label": label,
            "status": level,
            "feedback": feedback,
            "evidence_quote": quote[:300] or None,
            "evidence_status": evidence_status,
        }
    return result


def local_dimensions(answer: str, question_text: str = "") -> dict[str, dict[str, Any]]:
    """本地降级评价只使用回答中可观察到的信号，不推断经历真假。"""

    clean = answer.strip()
    snippet = clean[:120] or None
    if len(clean) < 12:
        levels = {key: "missing" for key, _ in DIMENSIONS}
    else:
        has_ownership = any(token in clean for token in ("我", "本人", "负责", "主导", "决定", "设计", "推动"))
        has_outcome = any(token in clean for token in ("结果", "最终", "提升", "降低", "完成", "上线", "验证", "复盘"))
        has_topic = bool(_evidence_tokens(question_text) & _evidence_tokens(clean))
        levels = {
            "relevance": "partial" if has_topic else "missing",
            "specificity": "partial",
            "ownership": "partial" if has_ownership else "missing",
            "outcome_evidence": "partial" if has_outcome else "missing",
            "communication": "partial",
        }
    feedbacks = {
        "relevance": "请明确回答与当前问题及目标岗位要求的直接关系。",
        "specificity": "补充具体场景、约束和关键细节，避免只列概念。",
        "ownership": "说清你本人负责的范围、判断和实际行动。",
        "outcome_evidence": "补充结果、指标或能够验证变化的事实。",
        "communication": "按背景、目标、行动、结果组织重点，减少跳跃。",
    }
    result = {
        key: {
            "label": label,
            "status": levels[key],
            "feedback": feedbacks[key],
            "evidence_quote": snippet if levels[key] != "missing" else None,
        }
        for key, label in DIMENSIONS
    }
    # 引用包含对应信号的句子，避免所有维度都机械引用同一段开头。
    sentences = [part.strip() for part in re.split(r"(?<=[。！？；\n])", clean) if part.strip()]
    signals = {
        "ownership": ("我", "本人", "负责", "主导", "决定", "设计", "推动"),
        "outcome_evidence": ("结果", "最终", "提升", "降低", "完成", "上线", "验证", "复盘"),
    }
    for key, words in signals.items():
        if result[key]["status"] != "missing":
            result[key]["evidence_quote"] = next((part[:300] for part in sentences if any(word in part for word in words)), snippet)
    return result


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
        "next_practice_focus": None,
    }
    if completion_type != "full" or len(contents) != 3:
        return base
    for item in contents:
        if (
            not isinstance(item, dict)
            or item.get("rubric_version") != RUBRIC_VERSION
            or item.get("schema_version") != FEEDBACK_SCHEMA_VERSION
            or not isinstance(item.get("evaluation_dimensions"), dict)
        ):
            return base
        for key, _ in DIMENSIONS:
            dimension = item["evaluation_dimensions"].get(key)
            if (
                not isinstance(dimension, dict)
                or not isinstance(dimension.get("status"), str)
                or dimension["status"] not in LEVEL_SCORES
                or dimension.get("evidence_status") == "unverified"
            ):
                return base
            if dimension["status"] != "missing" and not (
                isinstance(dimension.get("evidence_quote"), str) and dimension["evidence_quote"].strip()
            ):
                return base
    dimensions: dict[str, dict[str, Any]] = {}
    score_totals: dict[str, int] = {}
    for key, label in DIMENSIONS:
        scores = [LEVEL_SCORES[item["evaluation_dimensions"][key]["status"]] for item in contents]
        score_totals[key] = sum(scores)
        score = round(sum(scores) / len(contents))
        status = "strong" if score >= 75 else "partial" if score >= 40 else "missing"
        dimensions[key] = {"label": label, "status": status, "score": score}
    base["evaluation_dimensions"] = dimensions
    # 从原始等级一次计算，不能把已四舍五入的维度显示值再次平均。
    base["practice_index"] = round(sum(score_totals.values()) / (len(contents) * len(DIMENSIONS)))
    weakest_key = min(score_totals, key=score_totals.get)
    base["next_practice_focus"] = {
        "key": weakest_key,
        **dimensions[weakest_key],
        "suggestion": PRACTICE_SUGGESTIONS[weakest_key],
    }
    return base


def enrich_summary(content: dict[str, Any], main_feedback: list[dict[str, Any]], completion_type: str) -> dict[str, Any]:
    eligible_completion = completion_type if content.get("answered_main_count", 3) == 3 else "early"
    return {**content, **aggregate_main_feedback(main_feedback, eligible_completion)}
