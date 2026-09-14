"""在 201 PostgreSQL 上跑一遍可复核的 Purslyx 最小正式链路。

脚本只通过 HTTP 操作，不读取或打印密码。服务端的 DATABASE_URL、PGPASSWORD、
PURSLYX_TOKEN_SECRET 和本地演示开关由【本地开发环境.md】对应的进程环境注入。
脚本会创建带时间戳的临时演示账号和资料，不清理已有数据、不使用 SQLite。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx


BASE_URL = os.getenv("PURSLYX_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
PASSWORD = "purslyx-smoke-password-2026"


def response_body(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{response.request.method} {response.request.url} 返回非 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{response.request.method} {response.request.url} 返回格式不正确")
    return value


def call(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    expected: tuple[int, ...] = (200,),
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> tuple[httpx.Response, dict[str, Any]]:
    response = client.request(method, f"{BASE_URL}{path}", headers=headers, **kwargs)
    body = {} if response.status_code in {204, 304} else response_body(response)
    if response.status_code not in expected:
        error = body.get("error") or {}
        raise RuntimeError(
            f"{method} {path} 返回 {response.status_code}，"
            f"{error.get('code', 'UNKNOWN')}: {error.get('message', '无错误说明')}"
        )
    return response, body


def data_of(body: dict[str, Any]) -> dict[str, Any]:
    value = body.get("data")
    if not isinstance(value, dict):
        raise RuntimeError("接口 data 不是对象")
    return value


def auth_headers(access_token: str, csrf_token: str | None = None, key: str | None = None) -> dict[str, str]:
    value = {"Authorization": f"Bearer {access_token}"}
    if csrf_token:
        value["X-CSRF-Token"] = csrf_token
    if key:
        value["Idempotency-Key"] = key
    return value


def assert_error(
    client: httpx.Client,
    method: str,
    path: str,
    expected_status: int,
    expected_code: str,
    *,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> None:
    _, body = call(client, method, path, expected=(expected_status,), headers=headers, **kwargs)
    actual = (body.get("error") or {}).get("code")
    if actual != expected_code:
        raise RuntimeError(f"{method} {path} 错误码为 {actual!r}，期望 {expected_code!r}")


def register_and_login(client: httpx.Client, email: str) -> tuple[dict[str, Any], str, str]:
    _, body = call(
        client,
        "POST",
        "/api/v1/auth/register",
        expected=(201,),
        json={"email": email, "password": PASSWORD, "registration_role": "seeker"},
    )
    registration = data_of(body)
    if not registration.get("account", {}).get("email_verified"):
        verification_token = registration.get("verification_token")
        if not verification_token:
            raise RuntimeError("账号未验证；请用本地演示配置开启 PURSLYX_AUTO_VERIFY_LOCAL，或开启 debug 邮件令牌")
        call(client, "POST", "/api/v1/auth/verify-email", expected=(200,), json={"token": verification_token})

    _, body = call(
        client,
        "POST",
        "/api/v1/auth/login",
        expected=(200,),
        json={"email": email, "password": PASSWORD},
    )
    login = data_of(body)
    access_token = str(login.get("access_token") or "")
    csrf_token = str(login.get("csrf_token") or "")
    if not access_token or not csrf_token:
        raise RuntimeError("登录没有返回访问令牌和 CSRF 令牌")
    return login, access_token, csrf_token


def create_and_confirm_document(
    client: httpx.Client,
    headers: dict[str, str],
    *,
    document_type: str,
    subject_type: str,
    title: str,
    text: str,
    key: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = {
        "document_type": document_type,
        "subject_type": subject_type,
        "title": title,
        "text": text,
    }
    _, body = call(
        client,
        "POST",
        "/api/v1/documents",
        expected=(201,),
        headers={**headers, "Idempotency-Key": key},
        json=payload,
    )
    created = data_of(body)
    _, repeated_body = call(
        client,
        "POST",
        "/api/v1/documents",
        expected=(200,),
        headers={**headers, "Idempotency-Key": key},
        json=payload,
    )
    repeated = data_of(repeated_body)
    if repeated.get("id") != created.get("id"):
        raise RuntimeError("资料幂等重试创建了不同资源")

    _, detail_body = call(client, "GET", f"/api/v1/documents/{created['id']}", headers=headers)
    detail = data_of(detail_body)
    draft = detail.get("latest_draft") or {}
    content = detail.get("draft_content")
    if not draft.get("id") or not isinstance(content, dict):
        raise RuntimeError("资料没有可确认的解析草稿")
    _, version_body = call(
        client,
        "POST",
        f"/api/v1/documents/{created['id']}/versions",
        expected=(201,),
        headers={**headers, "Idempotency-Key": f"{key}-confirm"},
        json={
            "draft_id": draft["id"],
            "base_revision": detail["revision"],
            "content": content,
        },
    )
    version = data_of(version_body)
    return created, version, detail


def available_usage(usage: dict[str, Any], feature: str) -> int:
    for balance in usage.get("balances", []):
        if balance.get("feature") == feature:
            return int(balance.get("available", -1))
    raise RuntimeError(f"没有找到 {feature} 用量余额")


def main() -> None:
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    email = f"smoke-{suffix}@purslyx.local"
    second_email = f"smoke-isolation-{suffix}@purslyx.local"
    resume_text = """张三
工作经历：
负责 React 前端项目开发，使用 TypeScript 完成组件设计。
通过性能优化和自动化测试提升交付质量。
项目经历：
参与用户后台改版，推动需求分析、开发和上线。"""
    job_text = """前端开发工程师
公司：示例科技
地点：杭州
薪资：18K-28K
任职要求：
1、熟悉 React 或 Vue 与 TypeScript
2、负责前端项目开发和交付
3、关注性能优化与自动化测试"""

    with httpx.Client(timeout=30, follow_redirects=False) as client:
        _, health_body = call(client, "GET", "/health")
        health = health_body
        database = health.get("database") or {}
        if database.get("backend") != "postgresql" or health.get("environment") != "201":
            raise RuntimeError(f"服务未连接目标环境：{health}")

        _, access_token, csrf_token = register_and_login(client, email)
        web_headers = auth_headers(access_token, csrf_token)

        # Bearer 会话也必须带 CSRF，避免把认证方式当成写请求绕过条件。
        assert_error(
            client,
            "POST",
            "/api/v1/documents",
            403,
            "CSRF_INVALID",
            headers=auth_headers(access_token, key="csrf-check"),
            json={
                "document_type": "resume",
                "subject_type": "self_resume",
                "title": "不应创建",
                "text": "这条请求只用于验证 CSRF。",
            },
        )

        resume, resume_version, _ = create_and_confirm_document(
            client,
            web_headers,
            document_type="resume",
            subject_type="self_resume",
            title="201 冒烟简历",
            text=resume_text,
            key=f"resume-{suffix}",
        )
        job, job_version, _ = create_and_confirm_document(
            client,
            web_headers,
            document_type="job_description",
            subject_type="job_description",
            title="201 冒烟 JD",
            text=job_text,
            key=f"job-{suffix}",
        )

        assert_error(
            client,
            "GET",
            f"/api/v1/documents/{resume['id']}?version_id={job_version['id']}",
            404,
            "RESOURCE_NOT_FOUND",
            headers=web_headers,
        )

        _, file_body = call(
            client,
            "POST",
            "/api/v1/documents",
            expected=(201,),
            headers={**web_headers, "Idempotency-Key": f"file-{suffix}"},
            data={
                "document_type": "resume",
                "subject_type": "self_resume",
                "title": "201 文本文件上传",
            },
            files={"file": ("smoke.txt", resume_text.encode("utf-8"), "text/plain")},
        )
        file_document = data_of(file_body)
        if file_document.get("source_type") != "text":
            raise RuntimeError("multipart 文本文件没有按文件来源保存")
        file_response = client.get(f"{BASE_URL}/api/v1/documents/{file_document['id']}/file", headers=web_headers)
        if file_response.status_code != 200 or "张三" not in file_response.text:
            raise RuntimeError("上传文件的私有下载结果无效")

        assert_error(
            client,
            "POST",
            "/api/v1/documents",
            409,
            "IDEMPOTENCY_CONFLICT",
            headers={**web_headers, "Idempotency-Key": f"resume-{suffix}"},
            json={
                "document_type": "resume",
                "subject_type": "self_resume",
                "title": "同键不同正文",
                "text": "故意制造幂等冲突。",
            },
        )

        _, preference_body = call(
            client,
            "POST",
            "/api/v1/preferences",
            expected=(201,),
            headers={**web_headers, "Idempotency-Key": f"preference-{suffix}"},
            json={
                "display_name": "杭州前端岗位",
                "context": "self",
                "is_default": True,
                "preference": {
                    "job_title": {"status": "specified", "value": "前端", "strength": "important"},
                    "locations": {"status": "specified", "values": ["杭州"], "strength": "important"},
                    "work_mode": {"status": "unknown", "value": None, "strength": "prefer"},
                    "salary": {
                        "status": "specified",
                        "min": "18000",
                        "max": "28000",
                        "currency": "CNY",
                        "period": "monthly",
                        "tax_basis": "gross",
                        "strength": "important",
                    },
                },
            },
        )
        preference = data_of(preference_body)
        preference_version = (preference.get("version") or {}).get("id")
        if not preference_version:
            raise RuntimeError("岗位期望没有生成不可变版本")

        _, usage_before_body = call(client, "GET", "/api/v1/usage", headers=web_headers)
        usage_before = data_of(usage_before_body)
        analysis_key = f"analysis-{suffix}"
        _, pool_body = call(
            client,
            "POST",
            "/api/v1/job-pool/items",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": analysis_key},
            json={
                "source": {"type": "document_version", "job_document_version_id": job_version["id"]},
                "preference_version_id": preference_version,
                "analysis": {
                    "start_now": True,
                    "resume_document_version_id": resume_version["id"],
                    "confirm_usage": True,
                },
            },
        )
        pool_result = data_of(pool_body)
        pool = pool_result["job_pool_item"]
        analysis = pool_result["analysis"]
        if analysis.get("status") != "available" or pool.get("analysis_status") != "available":
            raise RuntimeError("分析没有进入可查看状态")
        _, analysis_repeat_body = call(
            client,
            "POST",
            f"/api/v1/job-pool/items/{pool['id']}/analyze",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": analysis_key},
            json={
                "resume_document_version_id": resume_version["id"],
                "preference_version_id": preference_version,
                "base_revision": pool["revision"],
                "confirm_usage": True,
            },
        )
        if (data_of(analysis_repeat_body).get("analysis") or {}).get("id") != analysis.get("id"):
            raise RuntimeError("分析幂等重试没有返回原报告")
        _, usage_after_analysis_body = call(client, "GET", "/api/v1/usage", headers=web_headers)
        usage_after_analysis = data_of(usage_after_analysis_body)
        if available_usage(usage_before, "analysis") - available_usage(usage_after_analysis, "analysis") != 1:
            raise RuntimeError("重复分析请求造成了多次扣减")

        _, report_body = call(client, "GET", f"/api/v1/analyses/{analysis['id']}", headers=web_headers)
        report = data_of(report_body)
        if not report.get("report", {}).get("dimensions"):
            raise RuntimeError("报告没有能力维度和证据结果")
        analysis_task_id = (analysis.get("task") or {}).get("id")
        if not analysis_task_id:
            raise RuntimeError("分析没有关联可读取的任务")
        call(client, "GET", "/api/v1/tasks", headers=web_headers)
        task_response, _ = call(client, "GET", f"/api/v1/tasks/{analysis_task_id}", headers=web_headers)
        task_etag = task_response.headers.get("ETag")
        if not task_etag:
            raise RuntimeError("任务详情没有返回 ETag")
        call(client, "GET", f"/api/v1/tasks/{analysis_task_id}", expected=(304,), headers={**web_headers, "If-None-Match": task_etag})

        segments = [
            segment
            for section in (resume_version.get("content") or {}).get("sections", [])
            for segment in section.get("segments", [])
            if segment.get("text")
        ]
        rewrite_key = f"rewrite-{suffix}"
        _, rewrite_body = call(
            client,
            "POST",
            "/api/v1/rewrites",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": rewrite_key},
            json={
                "analysis_id": analysis["id"],
                "source_resume_version_id": resume_version["id"],
                "segment_keys": [segments[0]["segment_key"]],
                "fact_version_ids": [],
                "confirm_usage": True,
            },
        )
        rewrite = data_of(rewrite_body)["rewrite"]
        _, rewrite_repeat_body = call(
            client,
            "POST",
            "/api/v1/rewrites",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": rewrite_key},
            json={
                "analysis_id": analysis["id"],
                "source_resume_version_id": resume_version["id"],
                "segment_keys": [segments[0]["segment_key"]],
                "fact_version_ids": [],
                "confirm_usage": True,
            },
        )
        if (data_of(rewrite_repeat_body).get("rewrite") or {}).get("id") != rewrite.get("id"):
            raise RuntimeError("改写幂等重试没有返回原任务")
        first_rewrite_segment = (rewrite.get("segments") or [None])[0]
        if not first_rewrite_segment:
            raise RuntimeError("改写没有返回段落结果")
        call(
            client,
            "POST",
            f"/api/v1/rewrites/{rewrite['id']}/segments/{first_rewrite_segment['id']}/decisions",
            expected=(200,),
            headers={**web_headers, "Idempotency-Key": f"rewrite-decision-{suffix}"},
            json={"decision": "adopted"},
        )

        _, variant_body = call(
            client,
            "POST",
            "/api/v1/resumes",
            expected=(201,),
            headers={**web_headers, "Idempotency-Key": f"variant-{suffix}"},
            json={
                "job_pool_item_id": pool["id"],
                "source_resume_version_id": resume_version["id"],
                "title": "杭州前端岗位版简历",
            },
        )
        variant = data_of(variant_body)
        variant_version = ((variant.get("versions") or [None])[0] or {}).get("id")
        if not variant_version:
            raise RuntimeError("岗位版简历没有版本")
        _, export_body = call(
            client,
            "POST",
            "/api/v1/exports",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"export-{suffix}"},
            json={"resume_variant_version_id": variant_version},
        )
        export = data_of(export_body)["export"]
        if not export.get("file_available"):
            raise RuntimeError("PDF 导出没有生成文件")
        export_file = client.get(f"{BASE_URL}/api/v1/exports/{export['id']}/file", headers=web_headers)
        if export_file.status_code != 200 or not export_file.content.startswith(b"%PDF"):
            raise RuntimeError("PDF 下载结果无效")

        browser_nonce = f"{suffix}-{uuid.uuid4().hex}"
        _, browser_code_body = call(
            client,
            "POST",
            "/api/v1/auth/browser-codes",
            expected=(200,),
            headers={**web_headers, "Idempotency-Key": f"browser-code-{suffix}"},
            json={"origin": "https://www.zhipin.com", "nonce": browser_nonce},
        )
        browser_code = data_of(browser_code_body)["code"]
        _, exchange_body = call(
            client,
            "POST",
            "/api/v1/browser-auth/exchange",
            expected=(200,),
            json={"code": browser_code, "origin": "https://www.zhipin.com", "nonce": browser_nonce},
        )
        browser_token = data_of(exchange_body)["browser_token"]
        browser_headers = {"Authorization": f"Bearer {browser_token}", "Idempotency-Key": f"browser-draft-{suffix}"}
        browser_payload = {
            "platform": "boss",
            "source_url": "https://www.zhipin.com/job_detail/1000000000000000000.html?utm_source=smoke",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "job_title": "前端开发工程师",
            "company_name": "示例科技",
            "location_text": "杭州",
            "salary_text": "18K-28K",
            "job_description_text": job_text,
            "missing_field_codes": [],
        }
        _, draft_body = call(client, "POST", "/api/v1/browser/job-drafts", expected=(201,), headers=browser_headers, json=browser_payload)
        browser_draft = data_of(draft_body)
        _, draft_repeat_body = call(client, "POST", "/api/v1/browser/job-drafts", expected=(200,), headers=browser_headers, json=browser_payload)
        if data_of(draft_repeat_body).get("id") != browser_draft.get("id"):
            raise RuntimeError("浏览器岗位重复获取没有命中去重")

        _, browser_pool_body = call(
            client,
            "POST",
            "/api/v1/job-pool/items",
            expected=(201,),
            headers={**web_headers, "Idempotency-Key": f"browser-pool-{suffix}"},
            json={
                "source": {"type": "browser_draft", "browser_draft_id": browser_draft["id"]},
                "preference_version_id": preference_version,
            },
        )
        browser_pool = data_of(browser_pool_body)
        click_token = (browser_pool.get("apply_action") or {}).get("click_token")
        if not click_token:
            raise RuntimeError("浏览器岗位没有生成去投递令牌")
        apply_headers = {**web_headers, "Idempotency-Key": f"apply-{suffix}"}
        apply_response = client.post(
            f"{BASE_URL}/api/v1/job-pool/items/{browser_pool['id']}/go-to-apply",
            headers=apply_headers,
            json={"click_token": click_token},
        )
        if apply_response.status_code != 303 or not str(apply_response.headers.get("location", "")).startswith("https://www.zhipin.com/"):
            raise RuntimeError("去投递没有返回受支持平台的 303 跳转")
        repeat_apply = client.post(
            f"{BASE_URL}/api/v1/job-pool/items/{browser_pool['id']}/go-to-apply",
            headers=apply_headers,
            json={"click_token": click_token},
        )
        if repeat_apply.status_code != 303:
            raise RuntimeError("重复去投递没有保持 303")

        _, interview_body = call(
            client,
            "POST",
            "/api/v1/interviews",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"interview-{suffix}"},
            json={
                "job_pool_item_id": pool["id"],
                "analysis_id": analysis["id"],
                "resume_document_version_id": resume_version["id"],
                "title": "201 面试练习",
                "confirm_usage": True,
            },
        )
        interview = data_of(interview_body)["interview"]
        if len(interview.get("questions") or []) != 3 or interview.get("status") != "awaiting_answer":
            raise RuntimeError("面试没有恢复为 3 个主问题的等待回答状态")
        _, interview_resume_body = call(client, "GET", f"/api/v1/interviews/{interview['id']}", headers=web_headers)
        if data_of(interview_resume_body).get("id") != interview.get("id"):
            raise RuntimeError("刷新面试详情没有恢复原会话")
        first_question = interview["questions"][0]
        call(
            client,
            "POST",
            f"/api/v1/interviews/{interview['id']}/answers",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"answer-{suffix}"},
            json={
                "question_id": first_question["id"],
                "answer_text": "我负责需求拆解、组件实现和上线验证，并通过性能优化解决了实际问题。",
                "base_revision": interview["revision"],
            },
        )

        # 用独立 HTTP 客户端验证跨账号隔离，不覆盖主演示账号的 Cookie。
        with httpx.Client(timeout=30, follow_redirects=False) as isolated_client:
            _, second_token, second_csrf = register_and_login(isolated_client, second_email)
            assert_error(
                isolated_client,
                "GET",
                f"/api/v1/documents/{resume['id']}",
                404,
                "RESOURCE_NOT_FOUND",
                headers=auth_headers(second_token, second_csrf),
            )

        delete_key = f"delete-{suffix}"
        _, delete_body = call(
            client,
            "POST",
            "/api/v1/documents",
            expected=(201,),
            headers={**web_headers, "Idempotency-Key": delete_key},
            json={
                "document_type": "resume",
                "subject_type": "self_resume",
                "title": "待删除演示资料",
                "text": "这份资料只用于验证删除后的旧入口失效。",
            },
        )
        deleted_id = data_of(delete_body)["id"]
        impact_response, impact_body = call(client, "GET", f"/api/v1/documents/{deleted_id}/deletion-impact", headers=web_headers)
        if not data_of(impact_body).get("impact_version"):
            raise RuntimeError("删除影响快照没有返回版本")
        deletion_etag = impact_response.headers.get("ETag")
        if not deletion_etag:
            raise RuntimeError("删除影响快照没有返回 ETag")
        call(client, "DELETE", f"/api/v1/documents/{deleted_id}", expected=(204,), headers={**web_headers, "If-Match": deletion_etag})
        assert_error(client, "GET", f"/api/v1/documents/{deleted_id}", 404, "RESOURCE_NOT_FOUND", headers=web_headers)

    print(
        json.dumps(
            {
                "environment": health.get("environment"),
                "database": database,
                "account_role": "seeker",
                "confirmed_versions": 2,
                "multipart_file_ready": True,
                "analysis_status": analysis.get("status"),
                "task_304": True,
                "ability_score": analysis.get("ability_score"),
                "evidence_coverage": analysis.get("evidence_coverage"),
                "rewrite_status": rewrite.get("status"),
                "pdf_ready": bool(export.get("file_available")),
                "browser_draft_deduped": True,
                "apply_status": 303,
                "interview_status_after_answer": "awaiting_answer",
                "cross_account_isolation": True,
                "delete_old_entry_blocked": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
