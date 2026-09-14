#!/usr/bin/env python3
"""验证 201 PostgreSQL Worker 的最小 HTTP 闭环。

运行前需用 ``PURSLYX_EXECUTION_MODE=worker scripts/start_201_local.sh`` 启动服务。
脚本不输出访问令牌、数据库密码或业务正文。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import httpx


BASE_URL = os.getenv("PURSLYX_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
PASSWORD = "purslyx-worker-smoke-password-2026"


def _expect(response: httpx.Response, *statuses: int) -> dict:
    statuses = statuses or (200,)
    if response.status_code not in statuses:
        raise RuntimeError(f"{response.request.method} {response.request.url.path} 返回 {response.status_code}")
    if response.status_code in {204, 304}:
        return {}
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("接口响应不是对象")
    return value


def main() -> None:
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    email = f"worker-smoke-{suffix}@purslyx.local"
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        health = _expect(client.get(f"{BASE_URL}/health"))
        if health.get("environment") != "201" or (health.get("database") or {}).get("backend") != "postgresql":
            raise RuntimeError("服务没有连接 201 PostgreSQL")
        registration = _expect(client.post(f"{BASE_URL}/api/v1/auth/register", json={"email": email, "password": PASSWORD, "registration_role": "seeker"}), 202)
        token = (registration.get("data") or {}).get("verification_token")
        if token:
            _expect(client.post(f"{BASE_URL}/api/v1/auth/verify-email", json={"token": token}), 200)
        login = _expect(client.post(f"{BASE_URL}/api/v1/auth/login", json={"email": email, "password": PASSWORD}), 200)
        login_data = login["data"]
        headers = {"Authorization": f"Bearer {login_data['access_token']}", "X-CSRF-Token": login_data["csrf_token"]}
        document = _expect(
            client.post(
                f"{BASE_URL}/api/v1/documents",
                headers={**headers, "Idempotency-Key": f"worker-document-{suffix}"},
                json={"document_type": "resume", "subject_type": "self_resume", "title": "Worker 201 验证", "text": "张三\n工作经历：负责前端开发和自动化测试。"},
            ),
            201,
        )["data"]
        parse = _expect(client.post(f"{BASE_URL}/api/v1/documents/{document['id']}/parse", headers={**headers, "Idempotency-Key": f"worker-parse-{suffix}"}), 202)["data"]
        if (parse.get("task") or {}).get("status") != "queued":
            raise RuntimeError("worker 模式下解析任务没有保持 queued")
        task_id = parse["task"]["id"]
        worker_command = [sys.executable, "scripts/worker_201.py", "--once", "--batch-size", "100"]
        for _ in range(20):
            subprocess.run(worker_command, cwd=os.path.dirname(os.path.dirname(__file__)), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            task = _expect(client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=headers), 200)["data"]
            if task.get("status") == "succeeded":
                break
        else:
            raise RuntimeError(f"Worker 任务未完成：{task.get('status')}")
        detail = _expect(client.get(f"{BASE_URL}/api/v1/documents/{document['id']}", headers=headers), 200)["data"]
        if not (detail.get("latest_draft") or {}).get("id"):
            raise RuntimeError("Worker 没有生成可确认解析草稿")
        print({"environment": "201", "database": "postgresql", "http_initial_status": "queued", "worker_final_status": task["status"]})


if __name__ == "__main__":
    main()
