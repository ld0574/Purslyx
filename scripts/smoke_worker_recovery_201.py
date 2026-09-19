#!/usr/bin/env python3
"""在 201 PostgreSQL 验证 Worker 租约过期后的自动恢复。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from datetime import timedelta

import httpx

BASE_URL = os.getenv("PURSLYX_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
PASSWORD = "purslyx-worker-recovery-password-2026"
PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))


def _data(response: httpx.Response, expected_status: int) -> dict:
    if response.status_code != expected_status:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} 返回 {response.status_code}"
        )
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("接口响应不是对象")
    return body.get("data") or body


def _captcha(client: httpx.Client) -> dict[str, str]:
    data = _data(client.get(f"{BASE_URL}/api/v1/auth/captcha"), 200)
    match = re.fullmatch(r"(\d+) \+ (\d+) = \?", str(data.get("question") or ""))
    if not match:
        raise RuntimeError("注册验证码题目格式不正确")
    return {"captcha_id": str(data["captcha_id"]), "captcha_answer": str(int(match[1]) + int(match[2]))}


def _run_worker_once() -> None:
    subprocess.run(
        [sys.executable, "scripts/worker_201.py", "--once", "--batch-size", "100"],
        cwd=PROJECT_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    email = f"worker-recovery-{suffix}@purslyx.local"
    with httpx.Client(timeout=30) as client:
        health = _data(client.get(f"{BASE_URL}/health"), 200)
        if health.get("environment") != "201" or (health.get("database") or {}).get("backend") != "postgresql":
            raise RuntimeError("服务必须连接 201 PostgreSQL")
        registration = _data(
            client.post(
                f"{BASE_URL}/api/v1/auth/register",
                json={
                    "email": email,
                    "password": PASSWORD,
                    "registration_role": "seeker",
                    **_captcha(client),
                },
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
        headers = {
            "Authorization": f"Bearer {login['access_token']}",
            "X-CSRF-Token": login["csrf_token"],
        }
        document = _data(
            client.post(
                f"{BASE_URL}/api/v1/documents",
                headers={**headers, "Idempotency-Key": f"recovery-document-{suffix}"},
                json={
                    "document_type": "resume",
                    "subject_type": "self_resume",
                    "title": "Worker 恢复验证",
                    "text": "工作经历：负责 Python API 开发和自动化测试。",
                },
            ),
            201,
        )
        parse_result = _data(
            client.post(
                f"{BASE_URL}/api/v1/documents/{document['id']}/parse",
                headers={**headers, "Idempotency-Key": f"recovery-parse-{suffix}"},
            ),
            202,
        )
        task_public_id = parse_result["task"]["id"]
        if parse_result["task"]["status"] != "queued":
            raise RuntimeError("恢复测试任务没有先进入 queued")

        # 只在当前进程读取环境文件，把任务构造成“Worker 已认领后进程退出”的事实。
        from worker_201 import _configure_environment

        _configure_environment()
        from sqlalchemy import select

        from server.app.db import session_scope
        from server.app.models import Task, TaskAttempt, TaskOutbox
        from server.app.services import utcnow

        crashed_at = utcnow() - timedelta(minutes=2)
        with session_scope() as db:
            task = db.scalar(select(Task).where(Task.public_id == task_public_id))
            if task is None:
                raise RuntimeError("恢复测试任务不存在")
            event = db.scalar(
                select(TaskOutbox).where(TaskOutbox.task_id == task.id).with_for_update()
            )
            if event is None:
                raise RuntimeError("恢复测试 outbox 不存在")
            task.status = "running"
            task.current_step = "running"
            task.started_at = crashed_at
            db.add(
                TaskAttempt(
                    task_id=task.id,
                    execution_generation=1,
                    lease_owner="simulated-crashed-worker",
                    lease_expires_at=crashed_at,
                    status="running",
                )
            )
            event.status = "claimed"
            event.claimed_by = "simulated-crashed-worker"
            event.claimed_at = crashed_at

        _run_worker_once()
        time.sleep(6)
        for _ in range(4):
            _run_worker_once()
            snapshot = _data(
                client.get(f"{BASE_URL}/api/v1/tasks/{task_public_id}", headers=headers),
                200,
            )
            if snapshot.get("status") == "succeeded":
                break
            time.sleep(1)
        else:
            raise RuntimeError("租约过期任务没有恢复成功")

        detail = _data(
            client.get(f"{BASE_URL}/api/v1/documents/{document['id']}", headers=headers),
            200,
        )
        if not (detail.get("latest_draft") or {}).get("id"):
            raise RuntimeError("恢复后的解析任务没有交付草稿")

        with session_scope() as db:
            task = db.scalar(select(Task).where(Task.public_id == task_public_id))
            attempts = list(
                db.scalars(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_id == task.id)
                    .order_by(TaskAttempt.execution_generation.asc())
                ).all()
            )
            statuses = [attempt.status for attempt in attempts]
            if statuses.count("expired") != 1 or statuses.count("succeeded") != 1:
                raise RuntimeError(f"恢复代次不符合预期：{statuses}")
            if task.retry_count != 1:
                raise RuntimeError("任务重试次数没有准确记录")

        print(
            {
                "environment": "201",
                "database": "postgresql",
                "expired_attempts": 1,
                "succeeded_attempts": 1,
                "retry_count": 1,
                "final_status": "succeeded",
            }
        )


if __name__ == "__main__":
    main()
