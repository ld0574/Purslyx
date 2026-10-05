"""真实 PostgreSQL 的隔离回归；仅由安全的 smoke 脚本启用。"""

from __future__ import annotations

import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import MetaData, func, select, text

pytestmark = pytest.mark.skipif(
    os.getenv("PURSLYX_TEST_POSTGRES") != "1",
    reason="Run scripts/smoke_interview_coaching_201.py with isolated PostgreSQL schemas",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_schema():
    from alembic import command
    from alembic.config import Config

    from server.app.db import Base, engine
    from server.app.models import Interview, InterviewPractice, Task

    expected = os.environ.get("PGOPTIONS", "")
    assert re.fullmatch(r"-c search_path=coaching_test_[a-f0-9]{32}", expected), (
        "Refuse any non-test schema"
    )
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_schema()")) == expected.split("=")[1]
    legacy = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name != InterviewPractice.__tablename__:
            table.to_metadata(legacy)
    sessions = legacy.tables[Interview.__tablename__]
    sessions.indexes = {
        index for index in sessions.indexes if "rubric_version" not in index.columns
    }
    sessions._columns.remove(sessions.c.rubric_version)
    tasks = legacy.tables[Task.__tablename__]
    for constraint in tasks.constraints:
        if constraint.name == "ck_async_tasks_type":
            constraint.sqltext = text(str(constraint.sqltext).replace(", 'interview_practice'", ""))
    legacy.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            sessions.insert().values(
                public_id="old-migration-record",
                account_id=0,
                job_pool_item_id=0,
                analysis_id=0,
                resume_version_id=0,
                title="旧版合成数据",
                status="completed",
                summary={"practice_index": 17},
            )
        )
    cfg = Config("alembic.ini")
    command.stamp(cfg, "0010_browser_pool_link")
    command.upgrade(cfg, "head")
    command.check(cfg)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT rubric_version FROM interview_sessions WHERE public_id='old-migration-record'"
                )
            )
            is None
        )
        assert (
            connection.scalar(
                text(
                    "SELECT summary->>'practice_index' FROM interview_sessions WHERE public_id='old-migration-record'"
                )
            )
            == "17"
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0011_interview_coaching"
        )


@pytest.fixture
def fixture():
    from fastapi.testclient import TestClient

    from server.app.db import SessionLocal
    from server.app.interview_rubric import RUBRIC_VERSION, enrich_summary
    from server.app.main import app
    from server.app.model_provider import ModelProvider
    from server.app.models import (
        Account,
        Analysis,
        Document,
        DocumentVersion,
        Interview,
        InterviewAnswer,
        InterviewFeedback,
        InterviewQuestion,
        InterviewSummary,
        JobPoolItem,
        WebSession,
    )
    from server.app.security import hash_secret, now_utc
    from server.app.services import grant_feature

    token, csrf = uuid.uuid4().hex, uuid.uuid4().hex
    provider = ModelProvider()
    with SessionLocal() as db:
        account = Account(
            email=f"{token}@coaching.test",
            email_normalized=f"{token}@coaching.test",
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
        grant_feature(db, account, "interview", 5, source_type="admin_grant", reason="隔离测试")
        resume = Document(
            account_id=account.id,
            title="合成简历",
            document_type="resume",
            subject_type="self_resume",
            source_type="text",
            status="confirmed",
        )
        job = Document(
            account_id=account.id,
            title="合成 JD",
            document_type="job_description",
            subject_type="job_description",
            source_type="text",
            status="confirmed",
        )
        db.add_all([resume, job])
        db.flush()
        rv = DocumentVersion(
            account_id=account.id,
            document_id=resume.id,
            version_no=1,
            content=provider.extract_resume("工作经历\n负责接口性能优化与监控验证").value,
        )
        jv = DocumentVersion(
            account_id=account.id,
            document_id=job.id,
            version_no=1,
            content=provider.extract_job(
                "工程师\n任职要求：负责接口性能优化\n岗位职责：负责质量验证"
            ).value,
        )
        db.add_all([rv, jv])
        db.flush()
        pool = JobPoolItem(
            account_id=account.id,
            source_type="manual",
            job_title="合成岗位",
            job_fields=jv.content["job_fields"],
            resume_version_id=rv.id,
            analysis_status="available",
        )
        db.add(pool)
        db.flush()
        analysis = Analysis(
            account_id=account.id,
            context_type="seeker_pool",
            status="available",
            resume_version_id=rv.id,
            job_version_id=jv.id,
            job_pool_item_id=pool.id,
            result=provider.analyze(rv.content, jv.content, None, "seeker_pool").value,
            completed_at=now_utc(),
        )
        db.add(analysis)
        db.flush()
        interview = Interview(
            account_id=account.id,
            analysis_id=analysis.id,
            resume_version_id=rv.id,
            job_pool_item_id=pool.id,
            title="合成完整练习",
            status="completed",
            rubric_version=RUBRIC_VERSION,
            revision=9,
        )
        db.add(interview)
        db.flush()
        questions, answers, contents = [], [], []
        for index, kind in enumerate(("experience", "reasoning", "scenario"), 1):
            question = InterviewQuestion(
                account_id=account.id,
                interview_id=interview.id,
                question_type="main",
                main_no=index,
                position_no=index,
                status="answered",
                question_text="请说明接口性能优化的依据、约束、行动与验证。",
                basis={"question_kind": kind},
            )
            db.add(question)
            db.flush()
            answer = InterviewAnswer(
                account_id=account.id,
                interview_id=interview.id,
                question_id=question.id,
                answer_text="我负责接口性能优化，比较方案和资源约束，通过测试、监控与复盘核验结果。"
                * 4,
            )
            db.add(answer)
            db.flush()
            content = provider.feedback(
                {
                    "question_type": "main",
                    "question_kind": kind,
                    "question_text": question.question_text,
                    "rubric_version": RUBRIC_VERSION,
                },
                answer.answer_text,
            ).value["content"]
            db.add(
                InterviewFeedback(
                    account_id=account.id,
                    interview_id=interview.id,
                    question_id=question.id,
                    status="available",
                    content=content,
                )
            )
            contents.append(content)
            questions.append(
                {
                    "id": question.public_id,
                    "question_type": "main",
                    "main_no": index,
                    "rubric_version": RUBRIC_VERSION,
                }
            )
            answers.append({"question_id": question.public_id, "answer_text": answer.answer_text})
        value = enrich_summary(provider.summary(questions, answers, "full").value, contents, "full")
        interview.summary = value
        db.add(
            InterviewSummary(
                account_id=account.id,
                interview_id=interview.id,
                completion_type="full",
                content=value,
            )
        )
        db.commit()
        values = {
            "account_id": account.id,
            "account_public_id": account.public_id,
            "interview": interview.public_id,
            "question": questions[0]["id"],
            "question2": questions[1]["id"],
            "pool": pool.public_id,
            "analysis": analysis.public_id,
            "resume": resume.public_id,
            "resume_version": rv.public_id,
            "original": answers[0]["answer_text"],
            "summary": value,
            "revision": 9,
        }
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    yield client, values
    client.close()


def checked(response, status=200):
    assert response.status_code == status, (
        f"Unexpected HTTP {response.status_code}, request_id={response.headers.get('X-Request-ID')}"
    )
    if status == 204:
        return {}
    return response.json()["data"]


def post_practice(client, values, key=None, question=None):
    return client.post(
        f"/api/v1/interviews/{values['interview']}/questions/{question or values['question']}/practice",
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
        json={
            "answer_text": "我会补充明确的约束和方案取舍，并通过故障注入、对照测试与监控验证判断。"
        },
    )


def set_worker(monkeypatch):
    from server.app import api, services
    from server.app.config import settings

    configuration = replace(settings, execution_mode="worker")
    monkeypatch.setattr(api, "settings", configuration)
    monkeypatch.setattr(services, "settings", configuration)


def assert_original_unchanged(client, values):
    from server.app.db import SessionLocal
    from server.app.models import UsageBalance, UsageReservation

    view = checked(client.get(f"/api/v1/interviews/{values['interview']}"))
    assert view["status"] == "completed" and view["revision"] == values["revision"]
    assert view["summary"]["content"] == values["summary"]
    assert view["questions"][0]["answer"]["answer_text"] == values["original"]
    assert view["task"] is None  # 训练任务不能成为会话当前任务。
    with SessionLocal() as db:
        balance = db.scalar(
            select(UsageBalance).where(
                UsageBalance.account_id == values["account_id"], UsageBalance.feature == "interview"
            )
        )
        assert (balance.consumed, balance.reserved) == (0, 0)
        assert (
            db.scalar(
                select(func.count(UsageReservation.id)).where(
                    UsageReservation.account_id == values["account_id"]
                )
            )
            == 0
        )


def test_inline_success_idempotency_single_opportunity_and_model_cost(fixture):
    from server.app.db import SessionLocal
    from server.app.models import ModelCall

    client, values = fixture
    key = uuid.uuid4().hex
    data = checked(post_practice(client, values, key), 202)
    assert data["practice"]["status"] == "available"
    assert data["task"]["usage_reservation"] is None
    assert data["task"]["result"]["interview_id"] == values["interview"]
    assert (
        checked(post_practice(client, values, key), 202)["practice"]["id"] == data["practice"]["id"]
    )
    assert post_practice(client, values).status_code == 409
    assert (
        len(checked(client.get(f"/api/v1/interviews/{values['interview']}/practices"))["items"])
        == 1
    )
    assert_original_unchanged(client, values)
    with SessionLocal() as db:
        calls = db.scalars(
            select(ModelCall).where(
                ModelCall.account_id == values["account_id"],
                ModelCall.feature == "interview_practice",
            )
        ).all()
        assert len(calls) == 1 and calls[0].cost_usd == 0


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_claims_have_one_database_fact(fixture, monkeypatch, same_key):
    from server.app.db import SessionLocal
    from server.app.models import InterviewPractice
    from server.app.worker import TaskWorker

    client, values = fixture
    set_worker(monkeypatch)
    key = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(post_practice, client, values, key if same_key else uuid.uuid4().hex)
            for _ in range(2)
        ]
        responses = [future.result(timeout=15) for future in futures]
    assert sorted(response.status_code for response in responses) == (
        [202, 202] if same_key else [202, 409]
    )
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count(InterviewPractice.id)).where(
                    InterviewPractice.account_id == values["account_id"]
                )
            )
            == 1
        )
    TaskWorker(owner="coaching-concurrency-test").run_once()
    assert_original_unchanged(client, values)


def test_failed_training_retries_same_task_and_answer_without_quota(fixture, monkeypatch):
    from server.app import api
    from server.app.errors import DomainError
    from server.app.model_provider import ModelProvider
    from server.app.worker import TaskWorker

    client, values = fixture
    set_worker(monkeypatch)
    data = checked(post_practice(client, values), 202)
    task_id = data["task"]["id"]

    class BrokenProvider(ModelProvider):
        def feedback(self, *_):
            raise DomainError("INTERVIEW_EVALUATION_INVALID", "评价暂未完成", 503, "retry")

    monkeypatch.setattr(api, "get_model_provider", BrokenProvider)
    TaskWorker(owner="coaching-failure-test").run_once()
    failed = checked(client.get(f"/api/v1/interviews/{values['interview']}/practices"))["items"][0]
    assert failed["status"] == "failed" and failed["task"]["retryable"]
    assert_original_unchanged(client, values)
    monkeypatch.setattr(api, "get_model_provider", ModelProvider)
    retry = checked(
        client.post(
            f"/api/v1/tasks/{task_id}/retry", headers={"Idempotency-Key": uuid.uuid4().hex}, json={}
        ),
        202,
    )
    assert retry["task"]["id"] == task_id
    TaskWorker(owner="coaching-retry-test").run_once()
    success = checked(client.get(f"/api/v1/interviews/{values['interview']}/practices"))["items"][0]
    assert (
        success["status"] == "available"
        and success["id"] == failed["id"]
        and success["answer_text"] == failed["answer_text"]
    )
    assert_original_unchanged(client, values)


def test_budget_failure_preserves_opportunity_and_never_calls_model(fixture, monkeypatch):
    from server.app import api, services
    from server.app.errors import DomainError

    client, values = fixture
    monkeypatch.setattr(
        api,
        "get_model_provider",
        lambda: pytest.fail("Budget rejection must happen before model call"),
    )

    def reject_budget(*_, **__):
        raise DomainError("BUDGET_LIMIT_REACHED", "全站预算不足", 429, "retry")

    monkeypatch.setattr(services, "reserve_budget", reject_budget)
    data = checked(post_practice(client, values), 202)
    assert data["practice"]["status"] == "failed"
    view = checked(client.get(f"/api/v1/interviews/{values['interview']}"))
    assert view["questions"][0]["practice_state"]["remaining"] == 1
    assert_original_unchanged(client, values)


@pytest.mark.parametrize("source", ["resume", "analysis", "pool", "interview"])
def test_source_deletion_cancels_training_and_prohibits_retry(fixture, monkeypatch, source):
    from server.app.db import SessionLocal
    from server.app.models import InterviewPractice
    from server.app.worker import TaskWorker

    client, values = fixture
    set_worker(monkeypatch)
    data = checked(post_practice(client, values), 202)
    route = {
        "resume": "documents",
        "analysis": "analyses",
        "pool": "job-pool/items",
        "interview": "interviews",
    }[source]
    path = f"/api/v1/{route}/{values[source]}"
    impact = checked(client.get(path + "/deletion-impact"))
    checked(client.delete(path, headers={"If-Match": '"' + impact["impact_version"] + '"'}), 204)
    task_id = data["task"]["id"]
    assert checked(client.get(f"/api/v1/tasks/{task_id}"))["status"] == "cancelled"
    assert client.post(
        f"/api/v1/tasks/{task_id}/retry", headers={"Idempotency-Key": uuid.uuid4().hex}, json={}
    ).status_code in {404, 409}
    TaskWorker(owner="coaching-delete-test").run_once()
    with SessionLocal() as db:
        row = db.scalar(
            select(InterviewPractice).where(InterviewPractice.public_id == data["practice"]["id"])
        )
        assert row.deleted_at is not None and row.feedback is None


def test_late_worker_cannot_restore_deleted_training(fixture, monkeypatch):
    from server.app import api
    from server.app.db import SessionLocal
    from server.app.model_provider import ModelProvider
    from server.app.models import InterviewPractice
    from server.app.worker import TaskWorker

    client, values = fixture
    set_worker(monkeypatch)
    data = checked(post_practice(client, values), 202)
    entered, released = threading.Event(), threading.Event()

    class SlowProvider(ModelProvider):
        def feedback(self, question, answer):
            entered.set()
            assert released.wait(15)
            return super().feedback(question, answer)

    monkeypatch.setattr(api, "get_model_provider", SlowProvider)
    thread = threading.Thread(
        target=lambda: TaskWorker(owner="coaching-late-test").run_once(), daemon=True
    )
    thread.start()
    try:
        assert entered.wait(15)
        path = f"/api/v1/interviews/{values['interview']}"
        impact = checked(client.get(path + "/deletion-impact"))
        checked(
            client.delete(path, headers={"If-Match": '"' + impact["impact_version"] + '"'}), 204
        )
    finally:
        released.set()
        thread.join(20)
    assert not thread.is_alive()
    with SessionLocal() as db:
        row = db.scalar(
            select(InterviewPractice).where(InterviewPractice.public_id == data["practice"]["id"])
        )
        assert row.deleted_at is not None and row.feedback is None and row.comparison is None
    assert checked(client.get(f"/api/v1/tasks/{data['task']['id']}"))["status"] == "cancelled"


def test_account_isolation_and_legacy_cohort_hidden(fixture):
    from server.app.db import SessionLocal
    from server.app.models import Account, Interview

    client, values = fixture
    with SessionLocal() as db:
        foreign = Account(
            email=f"{uuid.uuid4().hex}@coaching.test",
            email_normalized=uuid.uuid4().hex,
            password_hash="synthetic-unused",
            registration_role="seeker",
            status="active",
        )
        db.add(foreign)
        db.flush()
        legacy = Interview(
            account_id=values["account_id"],
            analysis_id=0,
            job_pool_item_id=0,
            resume_version_id=0,
            title="旧场次",
            status="completed",
            rubric_version=None,
        )
        other = Interview(
            account_id=foreign.id,
            analysis_id=0,
            job_pool_item_id=0,
            resume_version_id=0,
            title="其他账号",
            status="completed",
            rubric_version="interview-rubric-v2",
        )
        db.add_all([legacy, other])
        db.commit()
    assert other.public_id not in [
        row["id"] for row in checked(client.get("/api/v1/interviews"))["items"]
    ]
    assert legacy.public_id not in [
        row["id"] for row in checked(client.get("/api/v1/interviews"))["items"]
    ]
    assert checked(client.get(f"/api/v1/interviews/{legacy.public_id}"))["legacy"] is True
    assert client.get(f"/api/v1/interviews/{other.public_id}/practices").status_code == 404
    assert (
        client.post(
            f"/api/v1/interviews/{other.public_id}/questions/{values['question']}/practice",
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json={"answer_text": "合成回答"},
        ).status_code
        == 404
    )
    for days in (7, 14, 30):
        dashboard = checked(client.get(f"/api/v1/dashboard?days={days}"))
        assert (
            dashboard["counts"]["completed_interviews"] == 1
            and len(dashboard["practice_series"]) == days
        )
        assert sum(row["sessions"] for row in dashboard["practice_series"]) == 1


@pytest.mark.parametrize("invalid_content", [{"practice_index": True}, {"practice_index": -1}, {"practice_index": 101}, {"schema_version": "interview-summary-v2"}])
def test_dashboard_excludes_invalid_or_legacy_practice_indices(fixture, invalid_content):
    from server.app.db import SessionLocal
    from server.app.models import Interview, InterviewSummary

    client, values = fixture
    with SessionLocal() as db:
        interview = db.scalar(select(Interview).where(Interview.public_id == values["interview"]))
        summary = db.scalar(select(InterviewSummary).where(InterviewSummary.interview_id == interview.id))
        summary.content = {**summary.content, **invalid_content}
        db.commit()
    dashboard = checked(client.get("/api/v1/dashboard?days=14"))
    assert sum(row["sessions"] for row in dashboard["practice_series"]) == 0
    assert dashboard["latest_practice_dimensions"] is None


def test_worker_formal_three_question_workflow_frozen_kinds_and_single_usage(fixture, monkeypatch):
    from server.app.db import SessionLocal
    from server.app.models import UsageBalance
    from server.app.worker import TaskWorker

    client, values = fixture
    set_worker(monkeypatch)
    data = checked(
        client.post(
            "/api/v1/interviews",
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json={
                "job_pool_item_id": values["pool"],
                "analysis_id": values["analysis"],
                "resume_document_version_id": values["resume_version"],
                "title": "Worker 新版流程",
                "confirm_usage": True,
            },
        ),
        202,
    )
    path = f"/api/v1/interviews/{data['interview']['id']}"
    TaskWorker(owner="coaching-opening-test").run_once()
    view = checked(client.get(path))
    assert {row["question_kind"] for row in view["questions"]} == {
        "experience",
        "reasoning",
        "scenario",
    }
    for _ in range(3):
        checked(
            client.post(
                path + "/answers",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                json={
                    "question_id": view["current_question_id"],
                    "base_revision": view["revision"],
                    "answer_text": "我负责接口性能优化，先比较方案与资源约束，再明确取舍，通过测试和监控核验效果与风险。"
                    * 4,
                },
            ),
            202,
        )
        TaskWorker(owner="coaching-formal-feedback-test").run_once()
        view = checked(client.get(path))
    assert (
        view["status"] == "completed" and view["summary"]["content"]["practice_index"] is not None
    )
    assert view["summary"]["content"]["rubric_version"] == "interview-rubric-v2"
    assert all(
        row["feedback"]["content"]["star_assessment"] is None
        for row in view["questions"]
        if row["question_kind"] != "experience"
    )
    with SessionLocal() as db:
        balance = db.scalar(
            select(UsageBalance).where(
                UsageBalance.account_id == values["account_id"], UsageBalance.feature == "interview"
            )
        )
        assert (balance.consumed, balance.reserved) == (1, 0)
