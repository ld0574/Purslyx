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


def _run_worker_until(client: httpx.Client, headers: dict[str, str], task_id: str) -> dict:
    """让一个独立的 201 Worker 处理指定任务，并返回最终任务快照。"""

    worker_command = [sys.executable, "scripts/worker_201.py", "--once", "--batch-size", "100"]
    task: dict = {}
    for _ in range(20):
        subprocess.run(
            worker_command,
            cwd=os.path.dirname(os.path.dirname(__file__)),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        task = _expect(client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=headers), 200)["data"]
        if task.get("status") == "succeeded":
            return task
        if task.get("status") in {"failed", "needs_input"}:
            raise RuntimeError(f"Worker 任务失败：{task.get('failure') or task.get('status')}")
    raise RuntimeError(f"Worker 任务未完成：{task.get('status')}")


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
        task = _run_worker_until(client, headers, task_id)
        detail = _expect(client.get(f"{BASE_URL}/api/v1/documents/{document['id']}", headers=headers), 200)["data"]
        draft = detail.get("latest_draft") or {}
        if not draft.get("id") or not isinstance(detail.get("draft_content"), dict):
            raise RuntimeError("Worker 没有生成可确认解析草稿")
        version = _expect(
            client.post(
                f"{BASE_URL}/api/v1/documents/{document['id']}/versions",
                headers={**headers, "Idempotency-Key": f"worker-resume-confirm-{suffix}"},
                json={"draft_id": draft["id"], "base_revision": detail["revision"], "content": detail["draft_content"]},
            ),
            201,
        )["data"]

        job = _expect(
            client.post(
                f"{BASE_URL}/api/v1/documents",
                headers={**headers, "Idempotency-Key": f"worker-job-{suffix}"},
                json={
                    "document_type": "job_description",
                    "subject_type": "job_description",
                    "title": "Worker 201 岗位",
                    "text": "前端开发工程师\n公司：示例科技\n地点：杭州\n薪资：18K-28K\n任职要求：熟悉 React 和 TypeScript。",
                },
            ),
            201,
        )["data"]
        job_parse = _expect(
            client.post(
                f"{BASE_URL}/api/v1/documents/{job['id']}/parse",
                headers={**headers, "Idempotency-Key": f"worker-job-parse-{suffix}"},
            ),
            202,
        )["data"]
        _run_worker_until(client, headers, job_parse["task"]["id"])
        job_detail = _expect(client.get(f"{BASE_URL}/api/v1/documents/{job['id']}", headers=headers), 200)["data"]
        job_draft = job_detail.get("latest_draft") or {}
        job_version = _expect(
            client.post(
                f"{BASE_URL}/api/v1/documents/{job['id']}/versions",
                headers={**headers, "Idempotency-Key": f"worker-job-confirm-{suffix}"},
                json={"draft_id": job_draft["id"], "base_revision": job_detail["revision"], "content": job_detail["draft_content"]},
            ),
            201,
        )["data"]

        preference = _expect(
            client.post(
                f"{BASE_URL}/api/v1/preferences",
                headers={**headers, "Idempotency-Key": f"worker-preference-{suffix}"},
                json={
                    "display_name": "Worker 201 前端岗位",
                    "context": "self",
                    "is_default": True,
                    "preference": {
                        "job_title": {"status": "specified", "value": "前端", "strength": "important"},
                        "locations": {"status": "specified", "values": ["杭州"], "strength": "important"},
                        "work_mode": {"status": "unknown", "value": None, "strength": "prefer"},
                        "salary": {"status": "unknown", "min": None, "max": None, "currency": "CNY", "period": "monthly", "tax_basis": "gross", "strength": "prefer"},
                    },
                },
            ),
            201,
        )["data"]
        pool_result = _expect(
            client.post(
                f"{BASE_URL}/api/v1/job-pool/items",
                headers={**headers, "Idempotency-Key": f"worker-analysis-{suffix}"},
                json={
                    "source": {"type": "document_version", "job_document_version_id": job_version["id"]},
                    "preference_version_id": preference["version"]["id"],
                    "analysis": {"start_now": True, "resume_document_version_id": version["id"], "confirm_usage": True},
                },
            ),
            202,
        )["data"]
        analysis_task = pool_result["task"]
        if analysis_task["status"] != "queued":
            raise RuntimeError("worker 模式下分析任务没有保持 queued")
        _run_worker_until(client, headers, analysis_task["id"])
        analysis = _expect(client.get(f"{BASE_URL}/api/v1/analyses/{pool_result['analysis']['id']}", headers=headers), 200)["data"]
        pool = _expect(client.get(f"{BASE_URL}/api/v1/job-pool/items/{pool_result['job_pool_item']['id']}", headers=headers), 200)["data"]
        if analysis.get("status") != "available" or pool.get("analysis_status") != "available":
            raise RuntimeError("Worker 分析没有生成可查看报告")

        variant = _expect(
            client.post(
                f"{BASE_URL}/api/v1/resumes",
                headers={**headers, "Idempotency-Key": f"worker-variant-{suffix}"},
                json={"job_pool_item_id": pool["id"], "source_resume_version_id": version["id"], "title": "Worker 201 岗位版"},
            ),
            201,
        )["data"]
        variant_version = variant["versions"][0]["id"]
        export_result = _expect(
            client.post(
                f"{BASE_URL}/api/v1/exports",
                headers={**headers, "Idempotency-Key": f"worker-export-{suffix}"},
                json={"resume_variant_version_id": variant_version},
            ),
            202,
        )["data"]
        if export_result["task"]["status"] != "queued" or export_result["export"]["status"] != "queued":
            raise RuntimeError("worker 模式下 PDF 导出没有保持 queued")
        export_task = _run_worker_until(client, headers, export_result["task"]["id"])
        export = _expect(client.get(f"{BASE_URL}/api/v1/exports/{export_result['export']['id']}", headers=headers), 200)["data"]
        if export_task["status"] != "succeeded" or export.get("status") != "available" or not export.get("file_available"):
            raise RuntimeError("Worker 没有生成可下载 PDF")
        pdf = client.get(f"{BASE_URL}/api/v1/exports/{export['id']}/file", headers=headers)
        if pdf.status_code != 200 or not pdf.content.startswith(b"%PDF"):
            raise RuntimeError("Worker PDF 下载结果无效")

        print({"environment": "201", "database": "postgresql", "http_initial_status": "queued", "worker_final_status": task["status"], "worker_analysis_status": analysis["status"], "worker_pdf_status": export["status"]})


if __name__ == "__main__":
    main()
