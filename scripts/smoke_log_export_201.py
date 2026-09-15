#!/usr/bin/env python3
"""可选验证 201 PostgreSQL 日志导出的最小异步闭环。

服务必须先由 ``scripts/start_201_local.sh`` 启动；该脚本只通过 HTTP 访问服务，
不会读取《本地开发环境.md》或打印任何凭据。管理员登录信息必须由调用方显式注入：
``PURSLYX_ADMIN_EMAIL`` + ``PURSLYX_ADMIN_PASSWORD``，也可以直接注入短期的
``PURSLYX_ADMIN_TOKEN`` + ``PURSLYX_ADMIN_CSRF``。未注入时安全跳过，方便普通求职
演示不被管理员验收阻塞。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


PROJECT_DIR = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("PURSLYX_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
ADMIN_EMAIL = os.getenv("PURSLYX_ADMIN_EMAIL", "").strip()
ADMIN_PASSWORD = os.getenv("PURSLYX_ADMIN_PASSWORD", "")
ADMIN_TOKEN = os.getenv("PURSLYX_ADMIN_TOKEN", "").strip()
ADMIN_CSRF = os.getenv("PURSLYX_ADMIN_CSRF", "").strip()


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{response.request.method} {response.request.url.path} 返回非 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("接口响应格式不是对象")
    return value


def _expect(response: httpx.Response, *statuses: int) -> dict[str, Any]:
    expected = statuses or (200,)
    if response.status_code not in expected:
        body = _json(response)
        error = body.get("error") or {}
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} 返回 {response.status_code}，"
            f"{error.get('code', 'UNKNOWN')}"
        )
    if response.status_code in {204, 304}:
        return {}
    return _json(response)


def _data(body: dict[str, Any]) -> dict[str, Any]:
    value = body.get("data")
    if not isinstance(value, dict):
        raise RuntimeError("接口 data 不是对象")
    return value


def _error_code(response: httpx.Response) -> str | None:
    return str((_json(response).get("error") or {}).get("code") or "") or None


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    if ADMIN_TOKEN and ADMIN_CSRF:
        return {"Authorization": f"Bearer {ADMIN_TOKEN}", "X-CSRF-Token": ADMIN_CSRF}
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        raise RuntimeError("未提供管理员验收凭据")
    login = _expect(
        client.post(
            f"{BASE_URL}/api/v1/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        ),
        200,
    )
    data = _data(login)
    token = str(data.get("access_token") or "")
    csrf = str(data.get("csrf_token") or "")
    permissions = set(data.get("account", {}).get("admin_permissions", []))
    if not token or not csrf:
        raise RuntimeError("管理员登录未返回完整会话")
    if "admin.logs.export" not in permissions or "admin.logs.operations.read" not in permissions:
        raise RuntimeError("管理员账号缺少日志导出或操作日志查看权限")
    return {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}


def _run_worker_until(client: httpx.Client, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    command = [sys.executable, "scripts/worker_201.py", "--once", "--batch-size", "100"]
    for _ in range(20):
        subprocess.run(
            command,
            cwd=PROJECT_DIR,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        task = _data(_expect(client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=headers), 200))
        if task.get("status") == "succeeded":
            return task
        if task.get("status") in {"failed", "needs_input", "cancelled"}:
            raise RuntimeError(f"日志导出 Worker 任务未成功：{task.get('status')}")
    raise RuntimeError("日志导出 Worker 任务在 20 次轮询内未完成")


def main() -> None:
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        health = _expect(client.get(f"{BASE_URL}/health"))
        database = health.get("database") or {}
        if health.get("environment") != "201" or database.get("backend") != "postgresql":
            raise RuntimeError("服务没有连接 201 PostgreSQL")
        if not ((ADMIN_TOKEN and ADMIN_CSRF) or (ADMIN_EMAIL and ADMIN_PASSWORD)):
            print(json.dumps({"environment": "201", "database": "postgresql", "skipped": "admin_credentials_not_injected"}, ensure_ascii=False))
            return

        headers = _admin_headers(client)
        now = datetime.now(timezone.utc)
        key = f"log-export-smoke-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        payload = {
            "log_type": "operations",
            "export_format": "csv",
            "filters": {
                "created_from": (now - timedelta(days=1)).isoformat(),
                "created_to": (now + timedelta(minutes=1)).isoformat(),
            },
        }
        request_headers = {**headers, "Idempotency-Key": key}
        created_response = client.post(f"{BASE_URL}/api/v1/admin/log-exports", headers=request_headers, json=payload)
        created = _data(_expect(created_response, 202))
        export_id = str(created.get("id") or "")
        task = created.get("task") or {}
        task_id = str(task.get("id") or "")
        if not export_id or not task_id:
            raise RuntimeError("日志导出没有返回导出 ID 和任务 ID")

        repeated_response = client.post(f"{BASE_URL}/api/v1/admin/log-exports", headers=request_headers, json=payload)
        repeated = _data(_expect(repeated_response, 200, 202))
        if repeated.get("id") != export_id:
            raise RuntimeError("日志导出幂等重试创建了不同资源")

        conflict_payload = {**payload, "export_format": "jsonl"}
        conflict = client.post(f"{BASE_URL}/api/v1/admin/log-exports", headers=request_headers, json=conflict_payload)
        if conflict.status_code != 409 or _error_code(conflict) != "IDEMPOTENCY_CONFLICT":
            raise RuntimeError("日志导出同键不同请求没有返回幂等冲突")

        initial_status = str(created.get("status") or "")
        worker_ran = False
        if initial_status == "queued":
            _run_worker_until(client, headers, task_id)
            worker_ran = True
        elif initial_status != "downloadable":
            raise RuntimeError(f"日志导出初始状态异常：{initial_status}")

        final = _data(_expect(client.get(f"{BASE_URL}/api/v1/admin/log-exports/{export_id}", headers=headers), 200))
        if final.get("status") != "downloadable" or final.get("row_count") is None:
            raise RuntimeError("日志导出没有进入可下载状态或缺少行数")
        file_response = client.get(f"{BASE_URL}/api/v1/admin/log-exports/{export_id}/file", headers=headers)
        if file_response.status_code != 200 or not file_response.content.startswith("\ufeff".encode("utf-8")):
            raise RuntimeError("CSV 日志导出下载结果没有 UTF-8 BOM")
        if "purslyx-logs.csv" not in file_response.headers.get("content-disposition", ""):
            raise RuntimeError("日志导出下载文件名不符合约定")

        print(
            json.dumps(
                {
                    "environment": "201",
                    "database": "postgresql",
                    "http_status": created_response.status_code,
                    "initial_status": initial_status,
                    "worker_ran": worker_ran,
                    "final_status": final.get("status"),
                    "row_count": final.get("row_count"),
                    "idempotency_replayed": True,
                    "csv_downloadable": True,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
