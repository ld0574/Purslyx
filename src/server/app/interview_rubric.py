"""面试练习的稳定评价维度与聚合规则。"""

from __future__ import annotations

import logging
import re
from typing import Any

from .errors import DomainError
from .matching import _evidence_tokens
from .request_context import current_request_id

LEGACY_RUBRIC_VERSION = "interview-rubric-v1"
RUBRIC_VERSION = "interview-rubric-v2"
FEEDBACK_SCHEMA_VERSION = "interview-feedback-v5"
SUMMARY_SCHEMA_VERSION = "interview-summary-v3"
QUESTION_KINDS = ("experience", "reasoning", "scenario")

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


def normalise_dimensions(value: Any, *, answer: str = "", rubric_version: str = RUBRIC_VERSION) -> dict[str, dict[str, Any]]:
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
                current_request_id() or "unknown", rubric_version, key,
            )
            evidence_status = "unverified"
            level = "missing"
            quote = ""
            feedback = "该项评价字段不完整或证据未通过校验，暂时无法判断该维度。"
        if level != "missing" and (not quote or quote not in answer):
            logging.getLogger(__name__).warning(
                "interview evidence rejected request_id=%s rubric_version=%s stage=dimension_validation dimension=%s error_type=UnmatchedEvidence",
                current_request_id() or "unknown", rubric_version, key,
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


def local_dimensions(answer: str, question_text: str = "", question_kind: str = "experience") -> dict[str, dict[str, Any]]:
    """本地降级评价只使用回答中可观察到的信号，不推断经历真假。"""

    clean = answer.strip()
    snippet = clean[:120] or None
    if len(clean) < 12:
        levels = {key: "missing" for key, _ in DIMENSIONS}
    else:
        has_ownership = any(token in clean for token in ("我", "本人", "负责", "主导", "决定", "设计", "推动"))
        has_outcome = any(token in clean for token in ("结果", "最终", "提升", "降低", "完成", "上线", "验证", "复盘"))
        if question_kind != "experience":
            has_ownership = has_ownership or any(token in clean for token in ("选择", "取舍", "判断", "比较", "方案", "权衡"))
            has_outcome = has_outcome or any(token in clean for token in ("测试", "回滚", "监控", "核验", "检查", "假设"))
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
    if question_kind != "experience":
        feedbacks.update({
            "specificity": "请明确判断依据、假设和约束，避免只列概念。",
            "ownership": "请说明你本人的决策、方案取舍和理由。",
            "outcome_evidence": "请说明如何核验判断、监控风险或验证方案，不要求历史业绩。",
            "communication": "请先给结论，再说明依据、取舍与验证方式。",
        })
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


def aggregate_main_feedback(contents: list[dict[str, Any]], completion_type: str, rubric_version: str = RUBRIC_VERSION) -> dict[str, Any]:
    """只对三道完整主问题计算练习指数；追问和旧版反馈不会参与。"""

    base = {
        "rubric_version": rubric_version,
        "schema_version": SUMMARY_SCHEMA_VERSION if rubric_version == RUBRIC_VERSION else "interview-summary-v2",
        "practice_index": None,
        "evaluation_dimensions": None,
        "next_practice_focus": None,
    }
    if completion_type != "full" or len(contents) != 3:
        return base
    for item in contents:
        if (
            not isinstance(item, dict)
            or item.get("rubric_version") != rubric_version
            or item.get("schema_version") != (FEEDBACK_SCHEMA_VERSION if rubric_version == RUBRIC_VERSION else "interview-feedback-v4")
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
        "suggestion": dimension_action(question_kind(min(contents, key=lambda row: LEVEL_SCORES[row["evaluation_dimensions"][weakest_key]["status"]])), weakest_key) if rubric_version == RUBRIC_VERSION else PRACTICE_SUGGESTIONS[weakest_key],
    }
    return base


def enrich_summary(content: dict[str, Any], main_feedback: list[dict[str, Any]], completion_type: str, rubric_version: str = RUBRIC_VERSION) -> dict[str, Any]:
    eligible_completion = completion_type if content.get("answered_main_count", 3) == 3 else "early"
    return {**content, **aggregate_main_feedback(main_feedback, eligible_completion, rubric_version)}


def question_kind(question: dict[str, Any]) -> str:
    basis = question.get("basis") if isinstance(question.get("basis"), dict) else {}
    kind = question.get("question_kind") or basis.get("question_kind")
    return kind if isinstance(kind, str) and kind in QUESTION_KINDS else "experience"


def outline_prompts(kind: str) -> tuple[tuple[str, str], ...]:
    if kind == "reasoning":
        return (("结论", "请直接回答问题。"), ("依据", "请补充判断依据和约束。"), ("取舍", "请说明备选方案与选择理由。"), ("验证", "请说明如何核验判断和发现反例。"))
    if kind == "scenario":
        return (("目标", "请说明情景目标与假设。"), ("约束", "请明确资源、风险与边界。"), ("方案", "请说明你拟采取的行动和取舍。"), ("验证", "请说明验证、监控与风险应对。"))
    return (("背景", "请补充真实场景和约束。"), ("职责", "请说明本人负责的范围。"), ("行动", "请说明你的判断与具体行动。"), ("结果", "请补充真实结果或核验方式。"))


def dimension_action(kind: str, key: str) -> str:
    prompts = dict(outline_prompts(kind))
    actions = {
        "relevance": "请先用一句话直接回答当前问题，再解释与岗位能力的关系。",
        "specificity": prompts.get("依据") or prompts.get("约束") or prompts["背景"],
        "ownership": prompts.get("取舍") or prompts.get("方案") or prompts["职责"],
        "outcome_evidence": prompts.get("验证") or prompts["结果"],
        "communication": "请按结论、依据、行动或方案、验证方式组织回答，突出重点。",
    }
    return actions[key]


def practice_actions(kind: str, dimensions: dict[str, Any] | None) -> list[str]:
    order = sorted((key for key, _ in DIMENSIONS), key=lambda key: LEVEL_SCORES.get((dimensions or {}).get(key, {}).get("status"), 0))
    return [dimension_action(kind, key) for key in order[:2]]


def feedback_text(value: Any, answer: str, limit: int = 500) -> str:
    """反馈不是代写经历；内部枚举统一中文，原文引用不经过此转换。"""
    if not isinstance(value, str) or not value.strip():
        _invalid_feedback("InvalidNarrative")
    text = value.strip()
    narrative = re.sub(r"[“‘\"']([^”’\"']+)[”’\"']", lambda match: "" if match[1] in answer else match[0], text)
    if re.search(r"(?:^|[，。：；\s])(?:我|我们|本人)(?:曾|在|负责|主导|完成|实现|将|会|设计|推动|用了?|使用|选择|承担|通过|获得|取得|优化|解决|搭建|迁移|开发|建立)|\bI\s+(?:led|built|achieved|implemented|used|worked|designed|developed)\b", narrative, re.I):
        _invalid_feedback("InventedFirstPerson")
    if any(number not in answer for number in re.findall(r"\d+(?:\.\d+)?%?", text)):
        _invalid_feedback("UngroundedNumber")
    labels = {**dict(DIMENSIONS), "strong": "表现较好", "partial": "仍可加强", "missing": "信息不足",
              "experience": "经历题", "reasoning": "判断题", "scenario": "情景题",
              "situation": "背景", "task": "职责", "action": "行动", "result": "结果"}
    for key, label in labels.items():
        text = re.sub(rf"(?<![a-zA-Z_]){key}(?![a-zA-Z_])", label, text, flags=re.I)
    return text[:limit]


def _invalid_feedback(reason: str) -> None:
    logging.getLogger(__name__).warning(
        "interview evaluation invalid request_id=%s rubric_version=%s stage=validation error_type=%s",
        current_request_id() or "unknown", RUBRIC_VERSION, reason,
    )
    raise DomainError("INTERVIEW_EVALUATION_INVALID", "评价暂未完成，请重试原任务；已提交回答已保存", 503, "retry")


def validate_v2_content(raw: Any, question: dict[str, Any], answer: str) -> dict[str, Any]:
    """v2 不把模型异常降为用户 missing；任何评分引用失败都必须重试。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("summary"), str) or not raw["summary"].strip():
        _invalid_feedback("InvalidContent")
    kind = question_kind(question)
    followup = question.get("question_type") == "followup"
    dimensions = None
    review = None
    if followup:
        source = raw.get("followup_review")
        if raw.get("evaluation_dimensions") is not None or not isinstance(source, dict) or not isinstance(source.get("supplemented"), list) or not isinstance(source.get("remaining_questions"), list) or len(source["supplemented"]) > 4 or len(source["remaining_questions"]) > 2:
            _invalid_feedback("InvalidFollowupReview")
        quotes = []
        for item in source["supplemented"]:
            if not isinstance(item, dict):
                _invalid_feedback("InvalidFollowupEvidence")
            origin = item.get("evidence_source")
            quote = item.get("quote")
            source_answer = answer if origin == "answer" else (question.get("parent_answer_text") or "") if origin == "parent_answer" else ""
            if not isinstance(quote, str) or not quote.strip() or quote.strip() not in source_answer:
                _invalid_feedback("UnmatchedFollowupEvidence")
            quotes.append({"evidence_source": origin, "quote": quote.strip()[:300]})
        review = {"supplemented": quotes, "remaining_questions": [feedback_text(item, answer, 160) for item in source["remaining_questions"]]}
    else:
        source = raw.get("evaluation_dimensions")
        if raw.get("followup_review") is not None or not isinstance(source, dict) or set(source) != {key for key, _ in DIMENSIONS}:
            _invalid_feedback("IncompleteDimensions")
        for item in source.values():
            if not isinstance(item, dict) or not isinstance(item.get("status"), str) or item["status"] not in LEVEL_SCORES:
                _invalid_feedback("InvalidDimension")
            if item.get("evidence_status") == "unverified":
                _invalid_feedback("UnverifiedDimension")
            quote = item.get("evidence_quote")
            if item["status"] != "missing" and (not isinstance(quote, str) or not quote.strip() or quote.strip() not in answer):
                _invalid_feedback("UnmatchedDimensionEvidence")
        dimensions = normalise_dimensions(source, answer=answer)
        for key, item in source.items():
            dimensions[key]["feedback"] = feedback_text(item.get("feedback"), answer)
    if not isinstance(raw.get("knowledge_checks"), list) or len(raw["knowledge_checks"]) > 3:
        _invalid_feedback("InvalidKnowledgeChecks")
    checks = []
    for item in raw["knowledge_checks"]:
        if not isinstance(item, dict):
            _invalid_feedback("InvalidKnowledgeCheck")
        quote = item.get("claim_quote")
        if not isinstance(quote, str) or not quote.strip() or quote.strip() not in answer:
            _invalid_feedback("UnmatchedKnowledgeEvidence")
        note = feedback_text(item.get("note"), answer, 240)
        verification = feedback_text(item.get("verification"), answer, 160)
        if re.search(r"https?://|\[\d+\]|据.*(?:论文|文献)|权威(?:证明|认证)", note + verification):
            _invalid_feedback("InventedReference")
        checks.append({"claim_quote": quote.strip()[:300], "status": "needs_verification", "note": note, "verification": verification if verification.startswith("请") else "请核实：" + verification})
    outline = []
    prompts = dict(outline_prompts(kind))
    if not isinstance(raw.get("answer_outline"), list) or len(raw["answer_outline"]) > 4:
        _invalid_feedback("InvalidOutline")
    for item in raw["answer_outline"]:
        if not isinstance(item, dict) or item.get("kind") != "quote" or not isinstance(item.get("label"), str) or item["label"] not in prompts:
            _invalid_feedback("InvalidOutlineItem")
        quote = item.get("text")
        if not isinstance(quote, str) or not quote.strip() or quote.strip() not in answer:
            _invalid_feedback("UnmatchedOutlineEvidence")
        outline.append({"kind": "quote", "label": item["label"], "text": quote.strip()[:300]})
    if not outline and answer.strip():
        # 无可分段引用时仍保留本轮原文锚点，不把重答提纲全部变成空槽位。
        outline.append({"kind": "quote", "label": "原文", "text": answer.strip()[:300]})
    outline = outline[:4] + [{"kind": "prompt", "label": label, "text": prompt} for label, prompt in prompts.items()]
    star = raw.get("star_assessment") if kind == "experience" and not followup else None
    if kind == "experience" and not followup and (not isinstance(star, dict) or set(star) != {"situation", "task", "action", "result"}):
        _invalid_feedback("InvalidStarStructure")
    if star:
        for key, item in star.items():
            if not isinstance(item, dict) or not isinstance(item.get("status"), str) or item["status"] not in LEVEL_SCORES:
                _invalid_feedback("InvalidStarItem")
        star = {key: {"status": item["status"], "feedback": feedback_text(item.get("feedback"), answer)} for key, item in star.items()}
    conclusion = feedback_text(raw["summary"], answer, 300)
    conclusion = "".join(re.split(r"(?<=[。！？])", conclusion)[:2])
    return {
        "summary": conclusion, "question_kind": kind,
        "evaluation_dimensions": dimensions, "followup_review": review,
        "knowledge_checks": checks[:3], "priority_actions": practice_actions(kind, dimensions) if not followup else review["remaining_questions"],
        "answer_outline": outline, "star_assessment": star,
        "rubric_version": RUBRIC_VERSION, "schema_version": FEEDBACK_SCHEMA_VERSION, "review_status": "verified",
    }


def compare_practice(original: Any, updated: dict[str, Any]) -> dict[str, Any]:
    base = original if isinstance(original, dict) else {}
    valid = base.get("rubric_version") == RUBRIC_VERSION and base.get("schema_version") == FEEDBACK_SCHEMA_VERSION
    old = base.get("evaluation_dimensions") if isinstance(base.get("evaluation_dimensions"), dict) else {}
    new = updated.get("evaluation_dimensions") if isinstance(updated.get("evaluation_dimensions"), dict) else {}
    changes = {}
    for key, label in DIMENSIONS:
        before = old.get(key) if isinstance(old.get(key), dict) else {}
        after = new.get(key) if isinstance(new.get(key), dict) else {}
        comparable = valid and isinstance(before.get("status"), str) and before["status"] in LEVEL_SCORES and before.get("evidence_status") != "unverified" and (before["status"] == "missing" or bool(before.get("evidence_quote")))
        delta = LEVEL_SCORES[_level(after.get("status"))] - LEVEL_SCORES[_level(before.get("status"))]
        changes[key] = {"label": label, "before": before if comparable else None, "after": after,
                        "change": "unavailable" if not comparable else "improved" if delta > 0 else "weaker" if delta < 0 else "unchanged"}
    return {"baseline_available": all(row["change"] != "unavailable" for row in changes.values()), "dimensions": changes}
