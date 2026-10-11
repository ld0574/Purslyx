"""投递排序与自动处理的纯规则契约。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.api import AutoMatchingRequest
from server.app.application_recommendations import (
    RECOMMENDATION_VERSION,
    application_recommendation,
)
from server.app.auto_matching import MATCH_OUTPUT_LIMIT, PARSE_OUTPUT_LIMIT, token_bounds
from server.app.matching import build_match_result
from server.app.model_provider import OpenAIModelProvider
from server.app.parsing import parse_job_text, parse_resume_text


@pytest.mark.parametrize(
    "score,coverage,conditions,expected",
    [
        (90, 1, [], "priority"),
        (75, 0.7, [], "priority"),
        (74.9, 0.7, [], "consider"),
        (90, 0.69, [], "consider"),
        (55, 0.6, [], "consider"),
        (54.9, 1, [], "defer"),
        (None, 1, [], "verify"),
        (0, 1, [], "defer"),
        (100, 0.59, [], "verify"),
        (100, None, [], "verify"),
        (float("nan"), 1, [], "verify"),
        (101, 1, [], "verify"),
        (100, 1, [{"status": "conflicted", "strength": "required"}], "defer"),
        (100, 1, [{"status": "unknown", "strength": "required"}], "verify"),
        (
            100,
            1,
            [{"status": "unknown", "strength": "preferred", "explanation": "薪资口径需核实"}],
            "priority",
        ),
    ],
)
def test_tiers_respect_evidence_and_hard_conditions(score, coverage, conditions, expected):
    report = {"ability_score": score, "evidence_coverage": coverage, "conditions": conditions}
    result = application_recommendation(report)
    assert result["tier"] == expected
    assert result["version"] == RECOMMENDATION_VERSION
    assert "不代表招聘结果" in result["disclaimer"]
    assert "录用概率" not in result["reason"]
    assert result["risks"] == (
        ["薪资口径需核实"]
        if conditions and conditions[0].get("strength") == "preferred"
        else result["risks"]
    )


def test_only_existing_report_quotes_become_recommendation_strengths():
    text = "工作经历\n负责 React 和 TypeScript 项目交付"
    report = build_match_result(
        parse_resume_text(text),
        parse_job_text("前端工程师\n任职要求：熟悉 React\n岗位职责：负责 TypeScript 项目交付"),
    )
    recommendation = application_recommendation(report)
    assert recommendation["strengths"]
    assert all(row["evidence"] in text for row in recommendation["strengths"])
    assert all(row["segment_key"] for row in recommendation["strengths"])
    report["dimensions"][0]["requirements"].append(
        {"status": "supported", "job_quote": "虚构匹配", "evidence": []}
    )
    assert "虚构匹配" not in str(application_recommendation(report)["strengths"])


def test_automatic_budget_covers_both_calls_and_serialized_unicode():
    input_bound, output_bound = token_bounds("中文 JD" * 100)
    assert input_bound >= 320_000 + len(("中文 JD" * 100).encode("utf-8"))
    assert output_bound == PARSE_OUTPUT_LIMIT + MATCH_OUTPUT_LIMIT == 12000


@pytest.mark.parametrize(
    "changes",
    [
        {"daily_limit": 0},
        {"daily_limit": 51},
        {"base_revision": -1},
        {"auto_apply": True},
        {"preference_ids": ["p"] * 11},
    ],
)
def test_settings_have_closed_bounded_schema(changes):
    with pytest.raises(ValidationError):
        AutoMatchingRequest.model_validate({"enabled": True, "base_revision": 0, **changes})


def test_automatic_provider_uses_explicit_completion_limits():
    from test_model_provider import _provider

    provider, chat = _provider()
    assert isinstance(provider, OpenAIModelProvider)
    resume = parse_resume_text("工作经历\n负责 React 项目交付")
    job = provider.extract_job(
        "前端工程师\n熟悉 React\n负责 React 项目交付", max_output_tokens=PARSE_OUTPUT_LIMIT
    )
    provider.analyze(resume, job.value, None, "seeker_pool", max_output_tokens=MATCH_OUTPUT_LIMIT)
    assert [row["max_completion_tokens"] for row in chat.calls] == [4000, 8000]
    assert all("max_tokens" not in row for row in chat.calls)


def test_waiting_account_scan_rotates_past_long_term_blocked_accounts(monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from server.app import worker

    dispatched = []

    class ScanSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalars(self, statement):
            compiled = statement.compile(dialect=postgresql.dialect())
            query = str(compiled)
            values = list(range(1, 104))
            if "auto_match_jobs.account_id >" in query:
                values = [value for value in values if value > compiled.params["account_id_1"]]
            elif "auto_match_jobs.account_id =" in query:
                values = [compiled.params["account_id_1"]]
            assert "ORDER BY auto_match_jobs.account_id" in query
            return SimpleNamespace(all=lambda: values[:100])

        def commit(self):
            pass

    monkeypatch.setattr(worker, "SessionLocal", ScanSession)
    monkeypatch.setattr(worker, "dispatch_account", lambda db, value: dispatched.append(value))
    instance = worker.TaskWorker(owner="fair-scan-test")
    instance._schedule_automatic(None)
    assert dispatched == list(range(1, 101))
    instance._schedule_automatic(None)
    assert dispatched[100:] == [101, 102, 103]
    instance._schedule_automatic(None)
    assert dispatched[103:] == list(range(1, 101))
    instance._schedule_automatic(103)
    assert instance._automatic_account_cursor == 100
    assert dispatched[-1] == 103
    instance._schedule_automatic(None)
    assert dispatched[-3:] == [101, 102, 103]


def test_inline_background_tick_reuses_worker_cursor(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from server.app import worker

    supplied = SimpleNamespace(run_once=Mock(return_value=0), _automatic_account_cursor=103)
    constructor = Mock(side_effect=AssertionError("must reuse background worker"))
    monkeypatch.setattr(worker, "TaskWorker", constructor)
    worker.run_automatic_inline(worker=supplied)
    constructor.assert_not_called()
    supplied.run_once.assert_called_once_with(account_id=None, automatic_only=True)
    assert supplied._automatic_account_cursor == 103
