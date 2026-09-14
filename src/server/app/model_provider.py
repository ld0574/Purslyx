"""模型适配层。

本地默认实现是确定性的，保证没有 API Key 时也能完整跑通功能和测试。配置为 openai 时，
使用官方 Responses API；业务代码只依赖这里的结构化方法，方便以后切换兼容接口。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import settings
from .errors import DomainError
from .matching import build_match_result
from .parsing import parse_job_text, parse_resume_text


@dataclass
class ModelResult:
    value: dict[str, Any]
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class ModelProvider:
    """所有工作流共享的结构化模型接口。"""

    name = "local"

    def extract_resume(self, text: str) -> ModelResult:
        return ModelResult(parse_resume_text(text), self.name, settings.model_name)

    def extract_job(self, text: str) -> ModelResult:
        return ModelResult(parse_job_text(text), self.name, settings.model_name)

    def analyze(self, resume: dict[str, Any], job: dict[str, Any], preference: dict[str, Any] | None, context_type: str) -> ModelResult:
        return ModelResult(build_match_result(resume, job, preference, context_type), self.name, settings.model_name)

    def rewrite(
        self,
        segments: list[dict[str, Any]],
        job: dict[str, Any],
        facts: list[dict[str, Any]],
    ) -> ModelResult:
        target = (job.get("job_fields") or {}).get("title") or "目标岗位"
        fact_text = "；".join(str(item.get("fact_text", "")) for item in facts if item.get("fact_text"))
        result = []
        for segment in segments:
            original = str(segment.get("text", "")).strip()
            suggestion = original
            if original:
                # 本地模式只调整表达结构并提示证据，不新增数字或不存在的经历。
                suggestion = f"围绕{target}，{original}"
                if fact_text:
                    suggestion += f" 可结合已确认事实：{fact_text[:180]}"
                suggestion += "。"
            result.append(
                {
                    "source_segment_key": segment.get("segment_key"),
                    "original_text": original,
                    "suggested_text": suggestion,
                    "rationale": "保留原始经历，仅补充岗位语境；新增成果数字前需要本人提供依据。",
                    "evidence": [{"fact_text": item.get("fact_text"), "source_type": item.get("source_type")} for item in facts],
                    "current_decision": None,
                    "decision_no": 0,
                }
            )
        return ModelResult({"segments": result, "schema_version": "rewrite-result-v1"}, self.name, settings.model_name)

    def opening_questions(self, report: dict[str, Any], resume: dict[str, Any]) -> ModelResult:
        requirements = [
            item.get("job_quote")
            for dimension in report.get("dimensions", [])
            for item in dimension.get("requirements", [])
            if item.get("job_quote")
        ]
        requirements = requirements[:3] or ["请介绍一段与目标岗位最相关的经历。"]
        questions = []
        for index in range(3):
            requirement = requirements[index % len(requirements)]
            questions.append(
                {
                    "id": _uuid_like(index),
                    "question_type": "main",
                    "main_no": index + 1,
                    "parent_question_id": None,
                    "question_text": f"请结合你的真实经历，说明你如何应对“{requirement}”？",
                    "basis": {"job_requirement": requirement, "rule_version": "interview-question-basis-v1"},
                    "status": "awaiting_answer",
                    "answer": None,
                    "feedback": None,
                }
            )
        return ModelResult({"questions": questions, "schema_version": "interview-question-v1"}, self.name, settings.model_name)

    def feedback(self, question: dict[str, Any], answer: str) -> ModelResult:
        clean = answer.strip()
        if len(clean) < 12:
            content = {
                "strengths": ["已经开始回答问题"],
                "gaps": ["缺少具体行动、背景和可核验结果"],
                "suggestions": ["补充你承担的动作、使用的方法以及能够核对的结果"],
            }
            needs_followup = True
        else:
            content = {
                "strengths": ["回答包含了与问题相关的实际表达"],
                "gaps": ["可以进一步说明个人承担范围和结果"],
                "suggestions": ["按背景、行动、结果的顺序补充一到两个具体细节"],
            }
            needs_followup = False
        return ModelResult(
            {
                "status": "available",
                "content": content,
                "needs_followup": needs_followup,
                "schema_version": "interview-feedback-v1",
            },
            self.name,
            settings.model_name,
        )

    def summary(self, questions: list[dict[str, Any]], answers: list[dict[str, Any]], completion_type: str) -> ModelResult:
        answered = len([item for item in answers if item.get("answer_text")])
        unanswered = [item.get("main_no") for item in questions if not any(answer.get("question_id") == item.get("id") for answer in answers)]
        return ModelResult(
            {
                "completion_type": completion_type,
                "answered_main_count": answered,
                "unanswered_main_numbers": unanswered,
                "content": {
                    "summary": "已根据实际提交的回答生成练习总结。",
                    "next_steps": ["继续用具体背景、行动和结果组织回答。"],
                },
                "schema_version": "interview-summary-v1",
            },
            self.name,
            settings.model_name,
        )


class OpenAIModelProvider(ModelProvider):
    """可选的官方 Responses API 适配器。"""

    name = "openai"

    def __init__(self) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise DomainError("MODEL_PROVIDER_UNAVAILABLE", "未安装 OpenAI SDK，当前使用本地模型", 503) from exc
        if not settings.openai_api_key:
            raise DomainError("MODEL_PROVIDER_UNAVAILABLE", "未配置 OPENAI_API_KEY，当前使用本地模型", 503)
        kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self.client = OpenAI(**kwargs)

    def _json(self, instruction: str, value: str, schema_name: str, schema: dict[str, Any]) -> ModelResult:
        """调用 Responses 的结构化输出，失败时让任务进入可恢复状态。"""

        response = self.client.responses.create(
            model=settings.model_name,
            reasoning={"effort": "max"},
            store=False,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": instruction}],
                },
                {"role": "user", "content": [{"type": "input_text", "text": value}]},
            ],
            text={"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        )
        raw = getattr(response, "output_text", "")
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DomainError("MODEL_OUTPUT_INVALID", "模型没有返回可读取的结构化结果", 503, "retry") from exc
        usage = getattr(response, "usage", None)
        return ModelResult(
            parsed,
            self.name,
            settings.model_name,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
        )

    def extract_resume(self, text: str) -> ModelResult:
        # 业务仍会在落库前用本地解析器做结构合法性检查。
        schema = {"type": "object", "properties": {"sections": {"type": "array"}}, "required": ["sections"], "additionalProperties": True}
        return self._json("只整理输入中的简历内容，不编造经历。", text, "document-resume-v1", schema)

    def extract_job(self, text: str) -> ModelResult:
        schema = {"type": "object", "properties": {"job_fields": {"type": "object"}}, "required": ["job_fields"], "additionalProperties": True}
        return self._json("只整理输入中的岗位信息，缺失条件保留为空。", text, "document-job-v1", schema)


def _uuid_like(index: int) -> str:
    """本地演示用的稳定问题 ID；持久化前会在 Service 中替换为随机 UUID。"""

    return f"local-question-{index + 1}"


def get_model_provider() -> ModelProvider:
    if settings.model_provider.lower() == "openai":
        return OpenAIModelProvider()
    return ModelProvider()
