#!/usr/bin/env python3
"""在 201 PostgreSQL 验证计次预留、任务幂等和面试轮次并发。"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from typing import Any

import httpx
from migrate_201 import configure_environment

BASE_URL = os.getenv("PURSLYX_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
PASSWORD = "purslyx-concurrency-password-2026"


def _data(response: httpx.Response, expected_status: int) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise RuntimeError(f"{response.request.method} {response.request.url.path} 返回 {response.status_code}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("接口响应不是对象")
    return value.get("data") or value


def _register(suffix: str) -> tuple[str, dict[str, str]]:
    email = f"concurrency-interview-{suffix}@purslyx.local"
    with httpx.Client(timeout=30) as client:
        registration = _data(
            client.post(
                f"{BASE_URL}/api/v1/auth/register",
                json={"email": email, "password": PASSWORD, "registration_role": "seeker"},
            ),
            202,
        )
        if registration.get("verification_token"):
            _data(
                client.post(
                    f"{BASE_URL}/api/v1/auth/verify-email",
                    json={"token": registration["verification_token"]},
                ),
                200,
            )
        login = _data(
            client.post(
                f"{BASE_URL}/api/v1/auth/login",
                json={"email": email, "password": PASSWORD},
            ),
            200,
        )
    return email, {
        "Authorization": f"Bearer {login['access_token']}",
        "X-CSRF-Token": login["csrf_token"],
    }


def _create_interview_fixture(email: str, suffix: str) -> tuple[str, list[str], int]:
    from sqlalchemy import select

    from server.app.db import session_scope
    from server.app.models import (
        Account,
        Analysis,
        Document,
        DocumentVersion,
        Interview,
        InterviewQuestion,
        JobPoolItem,
    )

    with session_scope() as db:
        account = db.scalar(select(Account).where(Account.email_normalized == email))
        if account is None:
            raise RuntimeError("并发面试测试账号不存在")
        resume = Document(account_id=account.id, document_type="resume", subject_type="self_resume", title=f"并发简历 {suffix}", source_type="text", status="available", raw_text="负责 Python API 开发。")
        job = Document(account_id=account.id, document_type="job_description", subject_type="job_description", title=f"并发岗位 {suffix}", source_type="text", status="available", raw_text="后端工程师，要求 Python 与 PostgreSQL。")
        db.add(resume)
        db.add(job)
        db.flush()
        resume_version = DocumentVersion(account_id=account.id, document_id=resume.id, version_no=1, content={"sections": [{"section_key": "experience", "title": "经历", "segments": [{"segment_key": "exp-1", "text": "负责 Python API 开发。"}]}]})
        job_version = DocumentVersion(account_id=account.id, document_id=job.id, version_no=1, content={"job_fields": {"title": "后端工程师", "requirements": ["Python", "PostgreSQL"]}})
        db.add(resume_version)
        db.add(job_version)
        db.flush()
        pool = JobPoolItem(account_id=account.id, source_type="manual", job_title="后端工程师", job_fields=job_version.content["job_fields"], job_document_id=job.id, job_document_version_id=job_version.id, resume_version_id=resume_version.id, analysis_status="available")
        db.add(pool)
        db.flush()
        analysis = Analysis(account_id=account.id, context_type="seeker", status="available", job_pool_item_id=pool.id, resume_version_id=resume_version.id, job_version_id=job_version.id, result={"ability_score": 80, "dimensions": []})
        db.add(analysis)
        db.flush()
        interview = Interview(account_id=account.id, job_pool_item_id=pool.id, analysis_id=analysis.id, resume_version_id=resume_version.id, title=f"并发轮次 {suffix}", status="awaiting_answer", revision=1, usage_settled=True, questions=[], answers=[])
        db.add(interview)
        db.flush()
        questions: list[InterviewQuestion] = []
        for number in range(1, 4):
            question = InterviewQuestion(account_id=account.id, interview_id=interview.id, question_type="main", main_no=number, position_no=number, question_text=f"第 {number} 个并发问题", basis={"rule_version": "concurrency-smoke-v1"}, status="awaiting_answer")
            db.add(question)
            db.flush()
            questions.append(question)
        interview.current_question_id = questions[0].public_id
        return interview.public_id, [question.public_id for question in questions], interview.revision


def _post_answer(headers: dict[str, str], interview_id: str, payload: dict[str, Any], key: str, barrier: Barrier | None = None) -> tuple[int, dict[str, Any]]:
    if barrier is not None:
        barrier.wait(timeout=10)
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{BASE_URL}/api/v1/interviews/{interview_id}/answers",
            headers={**headers, "Idempotency-Key": key},
            json=payload,
        )
    value = response.json()
    return response.status_code, value.get("data") or value.get("error") or {}


def _exercise_interview_rounds(email: str, headers: dict[str, str], suffix: str) -> dict[str, Any]:
    from sqlalchemy import func, select

    from server.app.db import session_scope
    from server.app.models import Account, Interview, InterviewAnswer, InterviewFeedback, Task

    interview_id, questions, revision = _create_interview_fixture(email, f"ordered-{suffix}")
    out_of_order = _post_answer(
        headers,
        interview_id,
        {"question_id": questions[1], "answer_text": "越过当前问题提交的回答", "base_revision": revision},
        f"out-of-order-{suffix}",
    )
    if out_of_order[0] != 409 or out_of_order[1].get("code") != "INTERVIEW_ROUND_CONFLICT":
        raise RuntimeError("乱序面试回答没有被稳定拒绝")

    payload = {"question_id": questions[0], "answer_text": "我负责接口设计、数据库事务和上线后的回归验证。", "base_revision": revision}
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_post_answer, headers, interview_id, payload, f"same-answer-{suffix}", barrier) for _ in range(2)]
        results = [future.result(timeout=40) for future in futures]
    if [status for status, _ in results] != [202, 202]:
        raise RuntimeError(f"同幂等键回答重放状态异常：{[status for status, _ in results]}")
    answer_ids = {(value.get("answer") or {}).get("id") for _, value in results}
    if len(answer_ids) != 1 or None in answer_ids:
        raise RuntimeError("同幂等键回答没有返回同一事实")

    with session_scope() as db:
        account_id = db.scalar(select(Account.id).where(Account.email_normalized == email))
        interview_pk = db.scalar(select(Interview.id).where(Interview.public_id == interview_id))
        answer_count = db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.interview_id == interview_pk)) or 0
        feedback_count = db.scalar(select(func.count(InterviewFeedback.id)).where(InterviewFeedback.interview_id == interview_pk)) or 0
        task_count = db.scalar(select(func.count(Task.id)).where(Task.account_id == account_id, Task.task_type == "interview_feedback", Task.idempotency_key == f"same-answer-{suffix}")) or 0
    if (answer_count, feedback_count, task_count) != (1, 1, 1):
        raise RuntimeError(f"面试并发事实重复：{answer_count}/{feedback_count}/{task_count}")

    second_id, second_questions, second_revision = _create_interview_fixture(email, f"different-{suffix}")
    second_payload = {"question_id": second_questions[0], "answer_text": "我用不同请求键并发提交这一轮回答。", "base_revision": second_revision}
    different_barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_post_answer, headers, second_id, second_payload, f"different-answer-{suffix}-{index}", different_barrier)
            for index in range(2)
        ]
        statuses = sorted(future.result(timeout=40)[0] for future in futures)
    if statuses != [202, 409]:
        raise RuntimeError(f"不同幂等键并发回答状态异常：{statuses}")
    return {"out_of_order_rejected": True, "same_key_replayed": True, "different_key_single_winner": True}


def _exercise_usage_reservations(suffix: str) -> dict[str, Any]:
    from sqlalchemy import select

    from server.app.db import SessionLocal, session_scope
    from server.app.errors import DomainError
    from server.app.models import Account, Task, TaskOutbox, UsageReservation
    from server.app.services import (
        available_count,
        create_task,
        get_or_create_balance,
        grant_feature,
        release_feature,
        utcnow,
    )

    with session_scope() as db:
        account = Account(email=f"concurrency-usage-{suffix}@purslyx.local", email_normalized=f"concurrency-usage-{suffix}@purslyx.local", password_hash="test-only", registration_role="seeker", status="active", email_verified_at=datetime.now(timezone.utc))
        db.add(account)
        db.flush()
        grant_feature(db, account, "analysis", 1, source_type="test", reason="201 并发预留验收")
        account_id = account.id

    def reserve(key: str, barrier: Barrier) -> tuple[str, int | str, bool | str]:
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            barrier.wait(timeout=10)
            try:
                task, _, existed = create_task(db, account, "analysis", {"case": key}, feature="analysis", idempotency_key=key)
                db.commit()
                return "ok", task.id, existed
            except DomainError as error:
                db.rollback()
                return "error", error.status_code, error.code

    def cleanup() -> None:
        with session_scope() as db:
            tasks = list(db.scalars(select(Task).where(Task.account_id == account_id, Task.status == "queued")).all())
            for task in tasks:
                reservation = db.scalar(select(UsageReservation).where(UsageReservation.task_id == task.id, UsageReservation.status == "reserved"))
                if reservation is not None:
                    release_feature(db, reservation.id, "并发验收完成")
                task.status = "cancelled"
                task.current_step = "cancelled"
                task.completed_at = utcnow()
                for event in db.scalars(select(TaskOutbox).where(TaskOutbox.task_id == task.id, TaskOutbox.status.in_(["pending", "claimed"]))).all():
                    event.status = "published"
                    event.published_at = utcnow()
                    event.claimed_by = None
                    event.claimed_at = None

    same_barrier = Barrier(2)
    same_key = f"usage-same-{suffix}"
    with ThreadPoolExecutor(max_workers=2) as executor:
        same_results = [future.result(timeout=30) for future in [executor.submit(reserve, same_key, same_barrier) for _ in range(2)]]
    if {result[0] for result in same_results} != {"ok"} or len({result[1] for result in same_results}) != 1 or sorted(result[2] for result in same_results) != [False, True]:
        raise RuntimeError(f"同键任务没有幂等重放：{same_results}")
    cleanup()

    different_barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        different_results = [future.result(timeout=30) for future in [executor.submit(reserve, f"usage-distinct-{suffix}-{index}", different_barrier) for index in range(2)]]
    if sorted(result[0] for result in different_results) != ["error", "ok"]:
        raise RuntimeError(f"末次余额并发预留没有单一胜者：{different_results}")
    error = next(result for result in different_results if result[0] == "error")
    if error[1:] != (429, "USAGE_INSUFFICIENT"):
        raise RuntimeError(f"余额不足错误不符合契约：{error}")
    cleanup()
    with session_scope() as db:
        balance = get_or_create_balance(db, account_id, "analysis")
        if available_count(balance) != 1 or balance.reserved != 0:
            raise RuntimeError("并发验收清理后余额不守恒")
    return {"same_key_single_task": True, "last_balance_single_winner": True, "balance_restored": True}


def main() -> None:
    health = _data(httpx.get(f"{BASE_URL}/health", timeout=10), 200)
    if health.get("environment") != "201" or (health.get("database") or {}).get("backend") != "postgresql":
        raise RuntimeError("服务必须连接 201 PostgreSQL")
    configure_environment()
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    email, headers = _register(suffix)
    interview = _exercise_interview_rounds(email, headers, suffix)
    usage = _exercise_usage_reservations(suffix)
    print({"environment": "201", "database": "postgresql", "interview": interview, "usage": usage, "result": "passed"})


if __name__ == "__main__":
    main()
