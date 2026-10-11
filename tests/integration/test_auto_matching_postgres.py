"""自动抓取→排队→匹配→投递建议；只在随机隔离 PostgreSQL Schema 执行。"""

from __future__ import annotations

import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.skipif(
    os.getenv("PURSLYX_TEST_POSTGRES") != "1", reason="Use isolated smoke_interview_coaching_201.py"
)


@pytest.fixture(scope="module", autouse=True)
def migrated():
    from alembic import command
    from alembic.config import Config

    from server.app.db import engine

    expected = os.environ.get("PGOPTIONS", "")
    assert re.fullmatch(r"-c search_path=coaching_test_[a-f0-9]{32}", expected)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_schema()")) == expected.split("=")[1]
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def fx(monkeypatch):
    from fastapi.testclient import TestClient

    from server.app import api, services, worker
    from server.app.config import settings
    from server.app.db import SessionLocal
    from server.app.main import app
    from server.app.model_provider import ModelProvider
    from server.app.models import (
        Account,
        BrowserSession,
        Document,
        DocumentVersion,
        Preference,
        PreferenceVersion,
        WebSession,
    )
    from server.app.security import hash_secret, now_utc
    from server.app.services import grant_feature

    config = replace(settings, execution_mode="worker", model_provider="local")
    monkeypatch.setattr(api, "settings", config)
    monkeypatch.setattr(services, "settings", config)
    monkeypatch.setattr(worker, "settings", config)
    monkeypatch.setattr("server.app.auto_matching.settings", config)
    monkeypatch.setattr("server.app.budget.settings", config)
    provider = ModelProvider()
    monkeypatch.setattr(worker, "get_model_provider", lambda: provider)
    token, csrf, browser = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    with SessionLocal() as db:
        account = Account(
            email=f"{token}@automatic.test",
            email_normalized=f"{token}@automatic.test",
            password_hash="synthetic-unused",
            registration_role="seeker",
            status="active",
            email_verified_at=now_utc(),
        )
        db.add(account)
        db.flush()
        db.add(
            WebSession(
                account_id=account.id,
                token_hash=hash_secret(token),
                csrf_token_hash=hash_secret(csrf),
                expires_at=now_utc() + timedelta(days=1),
            )
        )
        db.add(
            BrowserSession(
                account_id=account.id,
                token_hash=hash_secret(browser),
                expires_at=now_utc() + timedelta(days=1),
            )
        )
        grant_feature(db, account, "analysis", 10, source_type="admin_grant", reason="合成自动测试")
        resume = Document(
            account_id=account.id,
            title="合成工程师简历",
            document_type="resume",
            subject_type="self_resume",
            source_type="text",
            status="confirmed",
        )
        db.add(resume)
        db.flush()
        version = DocumentVersion(
            account_id=account.id,
            document_id=resume.id,
            version_no=1,
            content=provider.extract_resume(
                "工作经历\n负责 React 和 TypeScript 项目交付\n负责接口性能优化与监控验证"
            ).value,
        )
        content = {
            "job_direction": {"status": "unrestricted"},
            "locations": {"status": "unrestricted"},
            "work_mode": {"status": "unrestricted"},
            "salary": {"status": "unrestricted"},
        }
        profile = Preference(
            account_id=account.id,
            display_name="合成岗位期望",
            content=content,
            source_type="self_confirmed",
            status="active",
        )
        db.add_all([version, profile])
        db.flush()
        pv = PreferenceVersion(
            account_id=account.id,
            preference_id=profile.id,
            version_no=1,
            content=content,
            source_type="self_confirmed",
        )
        db.add(pv)
        db.commit()
        values = {
            "account_id": account.id,
            "resume_id": resume.public_id,
            "resume_version_id": version.id,
            "preference_id": profile.public_id,
            "preference_version_id": pv.public_id,
        }
    return {
        **values,
        "client": TestClient(app),
        "headers": {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
        "browser_headers": {"Authorization": f"Bearer {browser}"},
        "provider": provider,
        "config": config,
    }


def enable(fx, **changes):
    key = changes.pop("key", uuid.uuid4().hex)
    body = {
        "enabled": True,
        "resume_document_id": fx["resume_id"],
        "preference_ids": [fx["preference_id"]],
        "daily_limit": 10,
        "base_revision": 0,
        "confirm_usage": True,
        **changes,
    }
    return fx["client"].put(
        "/api/v1/job-pool/automation",
        headers={**fx["headers"], "Idempotency-Key": key},
        json=body,
    )


def capture(fx, job="1", **changes):
    key = changes.pop("key", uuid.uuid4().hex)
    body = {
        "platform": "boss",
        "source_url": f"https://www.zhipin.com/job_detail/auto-{job}.html",
        "captured_at": "2026-10-11T03:00:00Z",
        "job_title": "合成前端工程师",
        "company_name": "合成公司",
        "job_description_text": "前端工程师\n任职要求：熟悉 React 和 TypeScript\n岗位职责：负责接口性能优化与监控验证",
        **changes,
    }
    return fx["client"].post(
        "/api/v1/browser/job-drafts",
        headers={
            **fx["browser_headers"],
            "Idempotency-Key": key,
            "X-Request-ID": "automatic-regression-request",
        },
        json=body,
    )


def run_worker(fx):
    from server.app.worker import TaskWorker

    return TaskWorker(owner=f"auto-test-{uuid.uuid4().hex}", batch_size=10).run_once(
        account_id=fx["account_id"], automatic_only=True
    )


def read_state(fx):
    from server.app.db import SessionLocal
    from server.app.models import Analysis, AutoMatchJob, Task, UsageBalance

    with SessionLocal() as db:
        rows = db.scalars(
            select(AutoMatchJob)
            .where(AutoMatchJob.account_id == fx["account_id"])
            .order_by(AutoMatchJob.id)
        ).all()
        balance = db.scalar(
            select(UsageBalance).where(
                UsageBalance.account_id == fx["account_id"], UsageBalance.feature == "analysis"
            )
        )
        tasks = db.scalars(select(Task).where(Task.account_id == fx["account_id"])).all()
        analyses = db.scalars(select(Analysis).where(Analysis.account_id == fx["account_id"])).all()
        return rows, balance, tasks, analyses


def test_opt_in_is_required_and_does_not_scan_history(fx):
    assert capture(fx).status_code == 201
    assert read_state(fx)[0] == []
    assert enable(fx, confirm_usage=False).status_code == 422
    assert enable(fx).status_code == 200
    assert read_state(fx)[0] == []
    # 已抓取记录重放也不补领授权。
    assert capture(fx).status_code == 200
    assert read_state(fx)[0] == []


def test_capture_runs_both_stages_and_returns_grounded_recommendations(fx):
    assert enable(fx).status_code == 200
    response = capture(fx)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["automatic_processing"]["status"] == "queued"
    assert "click_token" not in str(data)
    rows, balance, tasks, analyses = read_state(fx)
    assert balance.reserved == 1 and balance.consumed == 0
    assert tasks[0].input_data["_request_id"] == "automatic-regression-request"
    assert run_worker(fx) == 1
    rows, balance, tasks, analyses = read_state(fx)
    assert tasks[0].status == "succeeded"
    assert balance.reserved == 0 and balance.consumed == 1
    assert analyses[0].result["parsed_job"]["job_fields"]["requirements"]
    assert (
        analyses[0].result["application_recommendation"]["version"]
        == "application-recommendation-v1"
    )
    view = fx["client"].get("/api/v1/job-pool/recommendations", headers=fx["headers"])
    assert view.status_code == 200
    daily = view.json()["data"]
    assert daily["progress"]["completed"] == 1
    assert daily["items"][0]["id"] == data["job_pool_item"]["id"]
    assert daily["items"][0]["recommendation"]["strengths"]
    assert daily["items"][0]["apply_action"]["click_token"]
    assert daily["timezone"] == "Asia/Shanghai"


def test_concurrent_identical_and_changed_captures_claim_only_once(fx):
    assert enable(fx).status_code == 200
    barrier = threading.Barrier(2)

    def submit():
        barrier.wait()
        return capture(fx)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(row.status_code for row in responses) == [200, 201]
    assert (
        capture(
            fx, job_description_text="前端工程师\n任职要求：熟悉 React\n岗位职责：负责质量验证"
        ).status_code
        == 201
    )
    rows, balance, tasks, analyses = read_state(fx)
    assert len(rows) == len(tasks) == len(analyses) == 1
    assert balance.reserved == 1


def test_insufficient_quota_waits_and_worker_resumes_after_grant(fx):
    from server.app.db import SessionLocal
    from server.app.models import Account, UsageBalance
    from server.app.services import grant_feature

    with SessionLocal() as db:
        balance = db.scalar(
            select(UsageBalance).where(
                UsageBalance.account_id == fx["account_id"], UsageBalance.feature == "analysis"
            )
        )
        balance.consumed = balance.granted
        db.commit()
    assert enable(fx).status_code == 200
    assert (
        capture(fx).json()["data"]["automatic_processing"]["blocked_code"] == "USAGE_INSUFFICIENT"
    )
    assert read_state(fx)[2] == []
    assert run_worker(fx) == 0
    with SessionLocal() as db:
        grant_feature(
            db,
            db.get(Account, fx["account_id"]),
            "analysis",
            1,
            source_type="admin_grant",
            reason="合成补充",
        )
        db.commit()
    assert run_worker(fx) == 1
    assert read_state(fx)[1].consumed == 11
    assert run_worker(fx) == 0


def test_daily_limit_rolls_over_in_shanghai_and_frozen_inputs_stay(fx):
    from server.app.auto_matching import dispatch_account
    from server.app.db import SessionLocal
    from server.app.models import AutoMatchJob, DocumentVersion

    assert enable(fx, daily_limit=1).status_code == 200
    capture(fx, "first")
    capture(fx, "second")
    assert read_state(fx)[0][1].blocked_code == "AUTO_DAILY_LIMIT"
    with SessionLocal() as db:
        original = db.get(DocumentVersion, fx["resume_version_id"])
        db.add(
            DocumentVersion(
                account_id=fx["account_id"],
                document_id=original.document_id,
                version_no=2,
                content=original.content,
            )
        )
        rows = db.scalars(
            select(AutoMatchJob)
            .where(AutoMatchJob.account_id == fx["account_id"])
            .order_by(AutoMatchJob.id)
        ).all()
        rows[0].scheduled_date = "2026-10-10"
        db.flush()
        assert (
            dispatch_account(
                db, fx["account_id"], now=datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc)
            )
            == 1
        )
        db.commit()
    rows, _, tasks, _ = read_state(fx)
    assert rows[1].scheduled_date == "2026-10-11"
    assert all(row.resume_version_id == fx["resume_version_id"] for row in rows)
    assert len(tasks) == 2


def test_failed_model_releases_quota_does_not_repeat_and_retry_uses_same_task(fx, monkeypatch):
    from server.app.errors import DomainError
    from server.app.model_provider import ModelResult

    assert enable(fx).status_code == 200
    data = capture(fx).json()["data"]

    def fail(*args, **kwargs):
        error = DomainError("MODEL_OUTPUT_INVALID", "合成模型校验失败", 503, "retry")
        error.model_result = ModelResult({}, "local", fx["config"].model_name, 4, 2)
        raise error

    monkeypatch.setattr(fx["provider"], "analyze", fail)
    assert run_worker(fx) == 1
    rows, balance, tasks, analyses = read_state(fx)
    assert tasks[0].status == analyses[0].status == "failed"
    assert balance.consumed == balance.reserved == 0
    assert run_worker(fx) == 0
    from server.app.model_provider import ModelProvider

    monkeypatch.setattr("server.app.worker.get_model_provider", ModelProvider)
    result = fx["client"].post(
        f"/api/v1/tasks/{data['automatic_processing']['task_id']}/retry",
        headers={**fx["headers"], "Idempotency-Key": uuid.uuid4().hex},
        json={"reason": "synthetic_retry"},
    )
    assert result.status_code == 202
    assert run_worker(fx) == 1
    rows, balance, tasks, analyses = read_state(fx)
    assert len(tasks) == 1 and tasks[0].status == "succeeded"
    assert balance.consumed == 1 and balance.reserved == 0


@pytest.mark.parametrize("target", ["pool", "resume", "preference", "analysis"])
def test_deletion_cancels_queue_and_blocks_retries_and_recapture(fx, target):
    assert enable(fx).status_code == 200
    data = capture(fx).json()["data"]
    rows, _, _, analyses = read_state(fx)
    path = {
        "pool": f"/api/v1/job-pool/items/{data['job_pool_item']['id']}",
        "resume": f"/api/v1/documents/{fx['resume_id']}",
        "preference": f"/api/v1/preferences/{fx['preference_id']}",
        "analysis": f"/api/v1/analyses/{analyses[0].public_id}",
    }[target]
    headers = fx["headers"]
    if target != "preference":
        impact = fx["client"].get(f"{path}/deletion-impact", headers=headers).json()["data"]
        headers = {**headers, "If-Match": f'"{impact["impact_version"]}"'}
    assert fx["client"].delete(path, headers=headers).status_code in {200, 204}
    assert run_worker(fx) == 0
    rows, balance, tasks, _ = read_state(fx)
    assert rows[0].deleted_at and tasks[0].status == "cancelled"
    assert balance.reserved == balance.consumed == 0
    retry = fx["client"].post(
        f"/api/v1/tasks/{tasks[0].public_id}/retry",
        headers={**fx["headers"], "Idempotency-Key": uuid.uuid4().hex},
        json={},
    )
    assert retry.status_code in {404, 409}
    if target == "pool":
        assert capture(fx).status_code == 410


def test_other_accounts_cannot_use_sources_or_read_recommendations(fx):
    from server.app.db import SessionLocal
    from server.app.models import Account, Document

    with SessionLocal() as db:
        foreign = Account(
            email="synthetic-other@automatic.test",
            email_normalized="synthetic-other@automatic.test",
            password_hash="synthetic",
            registration_role="seeker",
            status="active",
        )
        db.add(foreign)
        db.flush()
        resume = Document(
            account_id=foreign.id,
            title="外部合成简历",
            document_type="resume",
            subject_type="self_resume",
            source_type="text",
            status="confirmed",
        )
        db.add(resume)
        db.commit()
        foreign_id = resume.public_id
    assert enable(fx, resume_document_id=foreign_id).status_code == 422
    assert (
        fx["client"]
        .get("/api/v1/job-pool/recommendations", headers=fx["browser_headers"])
        .status_code
        == 401
    )


def test_pause_preserves_waiting_and_new_capture_does_not_claim(fx):
    assert enable(fx, daily_limit=1).status_code == 200
    capture(fx, "first")
    capture(fx, "second")
    assert enable(fx, enabled=False, base_revision=1, daily_limit=1).status_code == 200
    capture(fx, "paused")
    rows, _, _, _ = read_state(fx)
    assert len(rows) == 2
    assert rows[1].state == "waiting" and rows[1].blocked_code == "AUTO_PAUSED"


def test_account_parallel_workers_cannot_call_two_models_at_once(fx, monkeypatch):
    from server.app.model_provider import ModelProvider

    assert enable(fx).status_code == 200
    capture(fx, "a")
    capture(fx, "b")
    entered, release = threading.Event(), threading.Event()
    provider = ModelProvider()
    original = provider.extract_job

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "extract_job", blocking)
    monkeypatch.setattr("server.app.worker.get_model_provider", lambda: provider)
    from server.app.worker import TaskWorker

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            TaskWorker(owner="concurrent-auto-one", batch_size=1).run_once,
            account_id=fx["account_id"],
            automatic_only=True,
        )
        assert entered.wait(10)
        second = pool.submit(
            TaskWorker(owner="concurrent-auto-two", batch_size=1).run_once,
            account_id=fx["account_id"],
            automatic_only=True,
        )
        assert second.result(timeout=10) == 1
        assert sorted(task.status for task in read_state(fx)[2]) == ["queued", "running"]
        release.set()
        first.result(timeout=10)


def test_revisit_with_same_content_key_ignores_capture_time_but_not_changed_content(fx):
    assert enable(fx).status_code == 200
    key = uuid.uuid4().hex
    assert capture(fx, key=key).status_code == 201
    assert capture(fx, key=key, captured_at="2026-10-12T03:00:00Z").status_code == 200
    assert capture(fx, key=key, salary_text="20K-30K").status_code == 409
    assert len(read_state(fx)[0]) == 1


def test_settings_replay_and_revision_conflicts_are_safe(fx):
    key = uuid.uuid4().hex
    assert enable(fx, key=key).status_code == 200
    assert enable(fx, key=key).status_code == 200
    assert enable(fx, key=key, daily_limit=11).status_code == 409
    assert enable(fx, daily_limit=11).status_code == 409
    assert (
        fx["client"]
        .get("/api/v1/job-pool/automation", headers=fx["headers"])
        .json()["data"]["daily_limit"]
        == 10
    )


def test_queue_pressure_retains_jobs_until_worker_frees_slots(fx):
    assert enable(fx).status_code == 200
    for index in range(5):
        assert capture(fx, str(index)).status_code == 201
    rows, balance, tasks, _ = read_state(fx)
    assert len(tasks) == 3 and balance.reserved == 3
    assert all(row.blocked_code == "TASK_QUEUE_LIMIT_REACHED" for row in rows[3:])
    assert run_worker(fx) == 3
    assert run_worker(fx) == 2
    assert read_state(fx)[1].consumed == 5


@pytest.mark.parametrize("stage", ["extract", "match"])
def test_deleted_during_model_never_writes_back_or_calls_next_stage(fx, monkeypatch, stage):
    from server.app.model_provider import ModelProvider

    assert enable(fx).status_code == 200
    data = capture(fx).json()["data"]
    entered, release = threading.Event(), threading.Event()
    provider = ModelProvider()
    called = []
    extract, match = provider.extract_job, provider.analyze

    def extract_work(*args, **kwargs):
        called.append("extract")
        if stage == "extract":
            entered.set()
            assert release.wait(10)
        return extract(*args, **kwargs)

    def match_work(*args, **kwargs):
        called.append("match")
        if stage == "match":
            entered.set()
            assert release.wait(10)
        return match(*args, **kwargs)

    monkeypatch.setattr(provider, "extract_job", extract_work)
    monkeypatch.setattr(provider, "analyze", match_work)
    monkeypatch.setattr("server.app.worker.get_model_provider", lambda: provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_worker, fx)
        assert entered.wait(10)
        path = f"/api/v1/job-pool/items/{data['job_pool_item']['id']}"
        impact = fx["client"].get(f"{path}/deletion-impact", headers=fx["headers"]).json()["data"]
        assert (
            fx["client"]
            .delete(path, headers={**fx["headers"], "If-Match": f'"{impact["impact_version"]}"'})
            .status_code
            == 204
        )
        release.set()
        future.result(timeout=10)
    rows, balance, tasks, analyses = read_state(fx)
    assert tasks[0].status == "cancelled" and analyses[0].deleted_at and analyses[0].result is None
    assert balance.consumed == balance.reserved == 0
    assert called == (["extract"] if stage == "extract" else ["extract", "match"])
    assert run_worker(fx) == 0


def test_inline_background_processes_only_authorized_account_tasks(fx, monkeypatch):
    from server.app import api, services, worker

    assert enable(fx).status_code == 200
    config = replace(fx["config"], execution_mode="inline")
    for module in (api, services, worker):
        monkeypatch.setattr(module, "settings", config)
    response = capture(fx)
    assert response.status_code == 201
    # 返回时是持久 queued 摘要，响应后的 Worker 已完成；刷新即可恢复看到结果。
    assert response.json()["data"]["automatic_processing"]["status"] == "queued"
    rows, balance, tasks, _ = read_state(fx)
    assert tasks[0].status == "succeeded" and balance.consumed == 1
    worker.run_automatic_inline(fx["account_id"])
    assert read_state(fx)[1].consumed == 1


def external_config(fx, monkeypatch, *, priced=True):
    from decimal import Decimal

    from server.app.db import SessionLocal
    from server.app.models import ModelPriceVersion
    from server.app.services import utcnow

    config = replace(
        fx["config"], model_provider="openai", model_name=f"synthetic-auto-{uuid.uuid4().hex}"
    )
    for module in ("api", "services", "worker", "auto_matching", "budget", "model_provider"):
        monkeypatch.setattr(f"server.app.{module}.settings", config)
    if priced:
        with SessionLocal() as db:
            db.add(
                ModelPriceVersion(
                    provider="openai",
                    model=config.model_name,
                    input_usd_per_million=Decimal("1"),
                    cached_input_usd_per_million=Decimal("1"),
                    output_usd_per_million=Decimal("2"),
                    effective_from=utcnow() - timedelta(minutes=1),
                )
            )
            db.commit()
    return config


def test_missing_model_price_preserves_waiting_without_call(fx, monkeypatch):
    external_config(fx, monkeypatch, priced=False)
    assert enable(fx).status_code == 200
    data = capture(fx).json()["data"]
    assert data["automatic_processing"]["blocked_code"] == "MODEL_PRICE_UNKNOWN"
    assert run_worker(fx) == 0 and read_state(fx)[2] == []
    assert read_state(fx)[1].reserved == 0


@pytest.mark.parametrize("failed", [False, True])
def test_both_model_calls_and_second_call_failures_keep_real_usage(fx, monkeypatch, failed):
    from decimal import Decimal

    from server.app.db import SessionLocal
    from server.app.errors import DomainError
    from server.app.model_provider import ModelProvider, ModelResult
    from server.app.models import ModelCall

    config = external_config(fx, monkeypatch)

    class SyntheticProvider(ModelProvider):
        name = "openai"

        def extract_job(self, *args, **kwargs):
            value = super().extract_job(*args, **kwargs).value
            return ModelResult(value, "openai", config.model_name, 100, 10)

        def analyze(self, *args, **kwargs):
            if failed:
                error = DomainError("MODEL_OUTPUT_INVALID", "合成私密错误不包含正文", 503, "retry")
                error.model_result = ModelResult({}, "openai", config.model_name, 200, 20)
                raise error
            return ModelResult(
                super().analyze(*args, **kwargs).value, "openai", config.model_name, 200, 20
            )

    monkeypatch.setattr("server.app.worker.get_model_provider", SyntheticProvider)
    assert enable(fx).status_code == 200
    capture(fx)
    assert run_worker(fx) == 1
    rows, balance, tasks, _ = read_state(fx)
    with SessionLocal() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.task_id == tasks[0].id))
        assert call.input_tokens == 300 and call.output_tokens == 30
        assert call.cost_usd == Decimal("0.00036")
        assert call.status == ("failed" if failed else "succeeded")
    assert balance.consumed == (0 if failed else 1) and balance.reserved == 0


def test_site_budget_insufficient_keeps_unclaimed_without_debit(fx, monkeypatch):
    from decimal import Decimal

    from server.app.budget import _bucket
    from server.app.db import SessionLocal
    from server.app.services import shanghai_date

    external_config(fx, monkeypatch)
    with SessionLocal() as db:
        bucket = _bucket(db, shanghai_date())
        previous = bucket.budget_usd
        bucket.budget_usd = Decimal("0.01")
        db.commit()
    try:
        assert enable(fx).status_code == 200
        assert (
            capture(fx).json()["data"]["automatic_processing"]["blocked_code"]
            == "BUDGET_LIMIT_REACHED"
        )
        assert read_state(fx)[1].reserved == 0 and read_state(fx)[2] == []
    finally:
        with SessionLocal() as db:
            _bucket(db, shanghai_date()).budget_usd = previous
            db.commit()


def test_json_preference_references_are_integrity_checked(fx):
    from server.app.db import SessionLocal
    from server.app.integrity import collect_integrity_findings
    from server.app.models import AutoMatchingConfig

    assert enable(fx).status_code == 200
    with SessionLocal() as db:
        config = db.scalar(
            select(AutoMatchingConfig).where(AutoMatchingConfig.account_id == fx["account_id"])
        )
        config.preference_ids = [str(uuid.uuid4())]
        db.flush()
        findings, _ = collect_integrity_findings(db)
        assert any(
            row["resource_public_id"] == config.public_id
            and row["details"]["rule"] == "auto_config.preferences"
            for row in findings
        )
        db.rollback()
