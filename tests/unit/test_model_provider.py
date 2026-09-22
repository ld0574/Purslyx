"""真实模型适配器的结构化契约测试，不发起外部网络请求。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from server.app.errors import DomainError
from server.app.model_provider import (
    ModelProvider,
    ModelResult,
    OpenAIModelProvider,
    merge_model_results,
)


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        schema_name = kwargs["response_format"]["json_schema"]["name"]
        values: dict[str, Any] = {
            "document_resume_v2": {
                "schema_version": "document-content-v1",
                "sections": [{
                    "section_key": "experience",
                    "section_type": "experience",
                    "title": "工作经历",
                    "position": 1,
                    "segments": [{"segment_key": "experience-1", "text": "负责 React 项目交付", "source": "user_confirmed", "source_line": 1}],
                }],
                "job_fields": None,
            },
            "document_job_v2": {
                "schema_version": "document-content-v1",
                "sections": [],
                "job_fields": {
                    "title": "前端工程师",
                    "company_name": "示例科技",
                    "location_text": "杭州",
                    "work_mode": "onsite",
                    "salary_text": "20K-30K/月",
                    "locations": ["杭州"],
                    "salary": {"status": "specified", "min": "20000", "max": "30000", "currency": "CNY", "period": "monthly", "tax_basis": "pre_tax", "salary_months": None},
                    "requirements": ["熟悉 React"],
                    "responsibilities": ["负责 React 项目交付"],
                    "category": "engineering",
                    "source_text": "",
                },
            },
            "analysis_result_v2": {
                "job_category": "engineering",
                "model_score": 84,
                "model_score_rationale": "后端交付证据较强，但 LLM 应用开发经历待补充。",
                "requirements": [{"requirement_id": "req-1", "dimension_key": "technical", "status": "supported", "match_score": 92, "evidence_segment_keys": ["experience-1"], "explanation": "有 React 项目交付证据。"}],
                "insights": {"summary": "有直接项目证据。", "strengths": ["React"], "risks": ["结果数字待补充"], "recommended_actions": ["补充结果"]},
            },
            "rewrite_result_v2": {
                "segments": [{"source_segment_key": "experience-1", "suggested_text": "负责 React 项目交付，推动跨团队协作。", "rationale": "突出岗位相关性。", "evidence_fact_ids": ["fact-1"]}],
            },
            "interview_opening_v2": {
                "questions": [
                    {"question_text": "请用 STAR 说明一次 React 项目交付。", "method": "STAR", "basis": {"requirement_ids": ["req-1"], "evidence_segment_keys": ["experience-1"], "reason": "验证交付过程。"}},
                    {"question_text": "请说明你在项目中的具体行动。", "method": "STAR", "basis": {"requirement_ids": ["req-1"], "evidence_segment_keys": ["experience-1"], "reason": "验证个人承担。"}},
                    {"question_text": "请说明项目结果如何核验。", "method": "STAR", "basis": {"requirement_ids": ["req-1"], "evidence_segment_keys": ["experience-1"], "reason": "验证结果证据。"}},
                ],
            },
            "interview_feedback_v3": {
                "content": {
                    "summary": "回答有具体项目，但结果证据不足。",
                    "strengths": ["回答有具体项目"],
                    "gaps": ["缺少结果"],
                    "suggestions": ["补充可核验指标"],
                    "star_assessment": {
                        "situation": {"status": "partial", "feedback": "背景还可以更清楚。"},
                        "task": {"status": "partial", "feedback": "目标还可以更明确。"},
                        "action": {"status": "strong", "feedback": "行动有具体项目依据。"},
                        "result": {"status": "missing", "feedback": "还没有结果。"},
                    },
                    "missing_details": ["结果指标"],
                    "answer_template": "我负责【任务】，通过【行动】取得【结果】。",
                },
                "needs_followup": True,
                "followup_question": "请补充这个项目的结果和核验方式。",
            },
            "interview_summary_v2": {
                "completion_type": "full",
                "answered_main_count": 3,
                "answered_followup_count": 1,
                "unanswered_main_numbers": [],
                "content": {
                    "summary": "回答能够说明项目背景，但结果证据仍需加强。",
                    "strengths": ["有实际项目"],
                    "gaps": ["结果不够量化"],
                    "next_steps": ["补充结果指标"],
                    "star_assessment": {
                        "situation": {"status": "strong", "feedback": "背景清楚"},
                        "task": {"status": "partial", "feedback": "目标可更明确"},
                        "action": {"status": "strong", "feedback": "行动具体"},
                        "result": {"status": "missing", "feedback": "缺少结果"},
                    },
                },
            },
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(values[schema_name], ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
        )


def _provider() -> tuple[OpenAIModelProvider, _FakeChatCompletions]:
    provider = object.__new__(OpenAIModelProvider)
    chat = _FakeChatCompletions()
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    return provider, chat


def test_openai_provider_runs_every_core_skill_through_structured_model() -> None:
    provider, chat = _provider()
    resume = provider.extract_resume("工作经历\n负责 React 项目交付").value
    job = provider.extract_job("前端工程师\n示例科技\n杭州\n20K-30K/月\n熟悉 React\n负责 React 项目交付").value
    report = provider.analyze(resume, job, None, "seeker_pool").value
    rewrite = provider.rewrite(resume["sections"][0]["segments"], job, [{"id": "fact-1", "fact_text": "推动跨团队协作", "source_type": "user_added"}]).value
    questions = provider.opening_questions(report, resume).value
    feedback = provider.feedback({"question_text": questions["questions"][0]["question_text"], "question_type": "main"}, "我负责了 React 项目交付").value
    summary = provider.summary(
        [{"id": "q1", "main_no": 1, "question_type": "main"}],
        [{"question_id": "q1", "answer_text": "我负责了 React 项目交付"}],
        "full",
    ).value

    assert report["prompt_version"] == "analysis-openai-v4"
    assert report["ai_insights"]["strengths"] == ["React"]
    assert report["ai_insights"]["model_score"] == 84.0
    assert rewrite["method"] == "evidence-constrained-rewrite"
    assert questions["method"] == "STAR"
    assert feedback["method"] == "STAR"
    assert feedback["content"]["summary"] == "回答有具体项目，但结果证据不足。"
    assert feedback["content"]["star_assessment"]["result"]["status"] == "missing"
    assert feedback["content"]["answer_template"] == "我负责【任务】，通过【行动】取得【结果】。"
    assert summary["content"]["star_assessment"]["result"]["status"] == "missing"
    assert [call["response_format"]["json_schema"]["name"] for call in chat.calls] == [
        "document_resume_v2",
        "document_job_v2",
        "analysis_result_v2",
        "rewrite_result_v2",
        "interview_opening_v2",
        "interview_feedback_v3",
        "interview_summary_v2",
    ]
    assert chat.calls[0]["messages"][0]["role"] == "system"
    assert chat.calls[0]["messages"][1]["role"] == "user"


def test_resume_model_omission_falls_back_to_complete_original_text() -> None:
    provider, _ = _provider()
    source = "工作经历\n负责 React 项目交付\n负责 Python 项目"
    result = provider.extract_resume(source)
    segments = [segment["text"] for section in result.value["sections"] for segment in section["segments"]]

    assert segments == ["负责 React 项目交付", "负责 Python 项目"]
    assert result.provider == "openai"
    assert result.input_tokens == 12
    assert result.output_tokens == 8


def test_resume_model_request_failure_falls_back_without_inventing_content(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = _provider()

    def failed_request(*_: Any, **__: Any) -> Any:
        raise DomainError("MODEL_PROVIDER_REQUEST_FAILED", "model unavailable", 503)

    provider._json = failed_request  # type: ignore[method-assign]
    with caplog.at_level("WARNING"):
        result = provider.extract_resume("工作经历\n负责原文项目 PRIVATE_RESUME_BODY")
    assert result.value["sections"][0]["segments"][0]["text"] == "负责原文项目 PRIVATE_RESUME_BODY"
    assert result.provider == "openai"
    assert result.input_tokens is None
    assert result.fallback_reason == "MODEL_PROVIDER_REQUEST_FAILED"
    assert "fallback stage=request code=MODEL_PROVIDER_REQUEST_FAILED" in caplog.text
    assert "PRIVATE_RESUME_BODY" not in caplog.text


def test_provider_request_error_logs_type_without_resume_body(caplog: pytest.LogCaptureFixture) -> None:
    provider, chat = _provider()

    def failed_request(**_: Any) -> Any:
        raise RuntimeError("PRIVATE_RESUME_BODY")

    chat.create = failed_request  # type: ignore[method-assign]
    with caplog.at_level("WARNING"):
        result = provider.extract_resume("工作经历\nPRIVATE_RESUME_BODY")
    assert result.fallback_reason == "MODEL_PROVIDER_REQUEST_FAILED"
    assert "model request failed operation=document_resume_v2 cause_type=RuntimeError provider_status=unknown" in caplog.text
    assert "PRIVATE_RESUME_BODY" not in caplog.text


def test_oversized_resume_model_input_uses_local_fallback_without_call() -> None:
    provider, chat = _provider()
    result = provider.extract_resume("负责" * 41_000)
    assert result.provider == "local"
    assert result.value["sections"][0]["segments"][0]["text"] == "负责" * 41_000
    assert chat.calls == []


def test_openai_rewrite_rejects_new_unverified_numbers() -> None:
    provider, _ = _provider()

    def fake_json(*_: Any, **__: Any):
        return SimpleNamespace(
            value={"segments": [{"source_segment_key": "experience-1", "suggested_text": "交付 99 个项目", "rationale": "", "evidence_fact_ids": []}]},
            model="test",
            input_tokens=1,
            output_tokens=1,
        )

    provider._json = fake_json  # type: ignore[method-assign]
    with pytest.raises(DomainError) as error:
        provider.rewrite([{"segment_key": "experience-1", "text": "负责项目交付"}], {}, [])
    assert error.value.code == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize(
    "suggested_text",
    ["负责 Kubernetes 项目交付", "负责 React 项目交付，服务腾讯客户"],
)
def test_openai_rewrite_rejects_unverified_entities_and_skipped_fact_ids(suggested_text: str) -> None:
    provider, _ = _provider()

    def fake_json(*_: Any, **__: Any):
        return SimpleNamespace(
            value={"segments": [{"source_segment_key": "experience-1", "suggested_text": suggested_text, "rationale": "", "evidence_fact_ids": ["missing-fact"]}]},
            model="test",
            input_tokens=1,
            output_tokens=1,
        )

    provider._json = fake_json  # type: ignore[method-assign]
    with pytest.raises(DomainError) as error:
        provider.rewrite([{"segment_key": "experience-1", "text": "负责 React 项目交付"}], {}, [])
    assert error.value.code == "MODEL_OUTPUT_INVALID"


def test_openai_job_salary_uses_source_amounts_and_does_not_trust_inferred_currency() -> None:
    provider, _ = _provider()
    job = provider.extract_job("前端工程师\n示例科技\n杭州\n20K-30K/月\n熟悉 React").value

    assert job["job_fields"]["salary"] == {
        "status": "specified",
        "min": "20000.0",
        "max": "30000.0",
        "currency": None,
        "period": "monthly",
        "tax_basis": None,
        "salary_months": None,
    }


def test_model_results_can_be_merged_for_feedback_and_summary_accounting() -> None:
    result = merge_model_results(
        ModelResult({"step": "feedback"}, "openai", "test", 12, 8),
        ModelResult({"step": "summary"}, "openai", "test", 20, 10),
        value={"feedback": {"step": "feedback"}, "summary": {"step": "summary"}},
    )

    assert result.value["summary"]["step"] == "summary"
    assert result.input_tokens == 32
    assert result.output_tokens == 18
    assert ModelProvider().feedback({"question_type": "main"}, "回答一个具体项目").value["method"] == "STAR"
