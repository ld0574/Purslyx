#!/usr/bin/env python3
"""验证 201 PostgreSQL Worker 的最小 HTTP 闭环。

运行前需用 ``PURSLYX_EXECUTION_MODE=worker scripts/start_201_local.sh`` 启动服务。
脚本不输出访问令牌、数据库密码或业务正文。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid

import httpx

BASE_URL = os.getenv("PURSLYX_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
PASSWORD = "purslyx-worker-smoke-password-2026"
PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))


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


def _run_worker_once(*, unavailable_model: bool = False) -> None:
    worker_env = dict(os.environ)
    if unavailable_model:
        worker_env["PURSLYX_MODEL_PROVIDER"] = "openai"
        worker_env.pop("OPENAI_API_KEY", None)
    else:
        worker_env["PURSLYX_MODEL_PROVIDER"] = "local"
    subprocess.run(
        [sys.executable, "scripts/worker_201.py", "--once", "--batch-size", "100"],
        cwd=PROJECT_DIR,
        env=worker_env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_worker_until(client: httpx.Client, headers: dict[str, str], task_id: str) -> dict:
    """让一个独立的 201 Worker 处理指定任务，并返回最终任务快照。"""

    task: dict = {}
    for _ in range(20):
        _run_worker_once()
        task = _expect(client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=headers), 200)["data"]
        if task.get("status") == "succeeded":
            return task
        if task.get("status") in {"failed", "needs_input"}:
            raise RuntimeError(f"Worker 任务失败：{task.get('failure') or task.get('status')}")
    raise RuntimeError(f"Worker 任务未完成：{task.get('status')}")


def _verify_late_worker_cannot_restore_deleted_document(
    client: httpx.Client,
    headers: dict[str, str],
    suffix: str,
) -> None:
    """暂停真实解析 Worker，在删除提交后恢复它，确认迟到结果整体回滚。"""

    document = _expect(
        client.post(
            f"{BASE_URL}/api/v1/documents",
            headers={**headers, "Idempotency-Key": f"late-document-{suffix}"},
            json={
                "document_type": "resume",
                "subject_type": "self_resume",
                "title": "迟到 Worker 删除验证",
                "text": "负责不会在删除后复活的任务结果。",
            },
        ),
        201,
    )["data"]
    parse = _expect(
        client.post(
            f"{BASE_URL}/api/v1/documents/{document['id']}/parse",
            headers={**headers, "Idempotency-Key": f"late-parse-{suffix}"},
        ),
        202,
    )["data"]
    task_id = parse["task"]["id"]

    from worker_201 import _configure_environment

    _configure_environment()
    from sqlalchemy import select

    from server.app import worker as worker_module
    from server.app.db import session_scope
    from server.app.model_provider import ModelProvider
    from server.app.models import Document, DocumentDraft, Task, TaskAttempt, TaskOutbox
    from server.app.services import utcnow

    owner = f"late-delete-{suffix}"
    with session_scope() as db:
        task = db.scalar(select(Task).where(Task.public_id == task_id))
        if task is None:
            raise RuntimeError("迟到 Worker 验证任务不存在")
        event = db.scalar(
            select(TaskOutbox)
            .where(TaskOutbox.task_id == task.id, TaskOutbox.status == "pending")
            .with_for_update()
        )
        if event is None:
            raise RuntimeError("迟到 Worker 验证 outbox 不存在")
        event.status = "claimed"
        event.claimed_by = owner
        event.claimed_at = utcnow()
        event_id = event.id

    started = threading.Event()
    release = threading.Event()

    class SlowProvider(ModelProvider):
        def extract_resume(self, text: str):  # type: ignore[no-untyped-def]
            started.set()
            if not release.wait(timeout=20):
                raise RuntimeError("等待删除提交超时")
            return super().extract_resume(text)

    original_provider = worker_module.get_model_provider
    worker_module.get_model_provider = lambda: SlowProvider()
    worker = worker_module.TaskWorker(owner=owner, batch_size=1)
    thread = threading.Thread(target=worker._process_event, args=(event_id,), daemon=True)
    try:
        thread.start()
        if not started.wait(timeout=20):
            raise RuntimeError("迟到 Worker 没有进入模型执行阶段")
        impact = _expect(
            client.get(
                f"{BASE_URL}/api/v1/documents/{document['id']}/deletion-impact",
                headers=headers,
            ),
            200,
        )["data"]
        _expect(
            client.delete(
                f"{BASE_URL}/api/v1/documents/{document['id']}",
                headers={**headers, "If-Match": f'"{impact["impact_version"]}"'},
            ),
            204,
        )
    finally:
        release.set()
        thread.join(timeout=20)
        worker_module.get_model_provider = original_provider
    if thread.is_alive():
        raise RuntimeError("迟到 Worker 没有停止")

    task = _expect(client.get(f"{BASE_URL}/api/v1/tasks/{task_id}", headers=headers), 200)["data"]
    if task.get("status") != "cancelled" or (task.get("failure") or {}).get("code") != "TASK_SOURCE_DELETED":
        raise RuntimeError("删除后迟到任务没有保持 cancelled")
    if client.get(f"{BASE_URL}/api/v1/documents/{document['id']}", headers=headers).status_code != 404:
        raise RuntimeError("迟到 Worker 复活了已删除资料")
    with session_scope() as db:
        row = db.scalar(select(Document).where(Document.public_id == document["id"]))
        task_row = db.scalar(select(Task).where(Task.public_id == task_id))
        attempts = list(db.scalars(select(TaskAttempt).where(TaskAttempt.task_id == task_row.id)).all()) if task_row else []
        draft_count = db.scalar(select(DocumentDraft).where(DocumentDraft.document_id == row.id, DocumentDraft.status == "unconfirmed")) if row else None
        if row is None or row.deleted_at is None or draft_count is not None or not attempts or any(value.status not in {"cancelled", "stale"} for value in attempts):
            raise RuntimeError("删除后数据库出现可见解析结果")


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
        completed_analysis_task = _expect(client.get(f"{BASE_URL}/api/v1/tasks/{analysis_task['id']}", headers=headers), 200)["data"]
        if (completed_analysis_task.get("result") or {}).get("resource_id") != analysis["id"]:
            raise RuntimeError("分析任务没有返回标准业务资源引用")

        interview_result = _expect(
            client.post(
                f"{BASE_URL}/api/v1/interviews",
                headers={**headers, "Idempotency-Key": f"worker-interview-{suffix}"},
                json={
                    "job_pool_item_id": pool["id"],
                    "analysis_id": analysis["id"],
                    "resume_document_version_id": version["id"],
                    "title": "Worker 反馈与总结恢复验证",
                    "confirm_usage": True,
                },
            ),
            202,
        )["data"]
        opening_task = interview_result["task"]
        interview_id = interview_result["interview"]["id"]
        if opening_task["status"] != "queued":
            raise RuntimeError("worker 模式下面试开场没有保持 queued")
        opening_finished = _run_worker_until(client, headers, opening_task["id"])
        if (opening_finished.get("result") or {}).get("resource_id") != interview_id:
            raise RuntimeError("面试开场任务没有返回标准业务资源引用")
        interview = _expect(client.get(f"{BASE_URL}/api/v1/interviews/{interview_id}", headers=headers), 200)["data"]
        question = next((value for value in interview.get("questions") or [] if value.get("id") == interview.get("current_question_id")), None)
        if question is None:
            raise RuntimeError("Worker 面试开场没有生成当前问题")

        feedback_result = _expect(
            client.post(
                f"{BASE_URL}/api/v1/interviews/{interview_id}/answers",
                headers={**headers, "Idempotency-Key": f"worker-feedback-{suffix}"},
                json={
                    "question_id": question["id"],
                    "answer_text": "我负责需求拆解、代码实现和上线验证，并根据真实反馈完成复盘。",
                    "base_revision": interview["revision"],
                },
            ),
            202,
        )["data"]
        feedback_task_id = feedback_result["interview"]["task"]["id"]
        _run_worker_once(unavailable_model=True)
        failed_feedback = _expect(client.get(f"{BASE_URL}/api/v1/tasks/{feedback_task_id}", headers=headers), 200)["data"]
        failed_interview = _expect(client.get(f"{BASE_URL}/api/v1/interviews/{interview_id}", headers=headers), 200)["data"]
        if failed_feedback.get("status") != "failed" or failed_interview.get("status") != "feedback_failed":
            raise RuntimeError("面试反馈供应商失败没有留下可恢复状态")
        retried_feedback = _expect(
            client.post(
                f"{BASE_URL}/api/v1/tasks/{feedback_task_id}/retry",
                headers={**headers, "Idempotency-Key": f"worker-feedback-retry-{suffix}"},
                json={"reason": "worker_smoke_feedback_retry"},
            ),
            202,
        )["data"]["task"]
        if retried_feedback.get("status") != "queued":
            raise RuntimeError("面试反馈失败任务没有重新排队")
        feedback_finished = _run_worker_until(client, headers, feedback_task_id)
        interview = _expect(client.get(f"{BASE_URL}/api/v1/interviews/{interview_id}", headers=headers), 200)["data"]
        if (feedback_finished.get("result") or {}).get("resource_id") != interview_id or interview.get("status") != "awaiting_answer":
            raise RuntimeError("面试反馈重试没有恢复会话")

        summary_result = _expect(
            client.post(
                f"{BASE_URL}/api/v1/interviews/{interview_id}/finish",
                headers={**headers, "Idempotency-Key": f"worker-summary-{suffix}"},
                json={"base_revision": interview["revision"]},
            ),
            202,
        )["data"]
        summary_task_id = summary_result["task"]["id"]
        if summary_result.get("status") != "processing" or summary_result["task"].get("status") != "queued":
            raise RuntimeError("worker 模式下面试总结没有保持 queued")
        _run_worker_once(unavailable_model=True)
        failed_summary = _expect(client.get(f"{BASE_URL}/api/v1/tasks/{summary_task_id}", headers=headers), 200)["data"]
        failed_interview = _expect(client.get(f"{BASE_URL}/api/v1/interviews/{interview_id}", headers=headers), 200)["data"]
        if failed_summary.get("status") != "failed" or failed_interview.get("status") != "summary_failed":
            raise RuntimeError("面试总结供应商失败没有留下可恢复状态")
        _expect(
            client.post(
                f"{BASE_URL}/api/v1/tasks/{summary_task_id}/retry",
                headers={**headers, "Idempotency-Key": f"worker-summary-retry-{suffix}"},
                json={"reason": "worker_smoke_summary_retry"},
            ),
            202,
        )
        summary_finished = _run_worker_until(client, headers, summary_task_id)
        interview = _expect(client.get(f"{BASE_URL}/api/v1/interviews/{interview_id}", headers=headers), 200)["data"]
        if (summary_finished.get("result") or {}).get("resource_id") != interview_id or interview.get("status") != "ended_early" or (interview.get("summary") or {}).get("completion_type") != "early":
            raise RuntimeError("面试总结重试没有生成 early 总结")

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

        _verify_late_worker_cannot_restore_deleted_document(client, headers, suffix)

        print({"environment": "201", "database": "postgresql", "http_initial_status": "queued", "worker_final_status": task["status"], "worker_analysis_status": analysis["status"], "worker_feedback_retry": "succeeded", "worker_summary_retry": "succeeded", "late_worker_after_delete": "cancelled", "worker_pdf_status": export["status"]})


if __name__ == "__main__":
    main()
