"""在 201 PostgreSQL 上跑一遍可复核的 Purslyx 最小正式链路。

脚本只通过 HTTP 操作，不读取或打印密码。服务端的 DATABASE_URL、PGPASSWORD、
PURSLYX_TOKEN_SECRET 和本地开发开关由【本地开发环境.md】对应的进程环境注入。
脚本会创建带时间戳的临时验收账号和资料，不清理已有数据、不使用 SQLite。
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
        expected=(202,),
        json={"email": email, "password": PASSWORD, "registration_role": "seeker"},
    )
    registration = data_of(body)
    if registration.get("status") != "verification_requested":
        raise RuntimeError("注册没有返回中性受理状态")
    verification_token = registration.get("verification_token")
    if verification_token:
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
    invalid_login = client.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"email": email, "password": "wrong-purslyx-password-2026"},
    )
    invalid_body = response_body(invalid_login)
    invalid_error = invalid_body.get("error") or {}
    if invalid_login.status_code != 401 or invalid_error.get("code") != "AUTH_INVALID_CREDENTIALS":
        raise RuntimeError("错误凭据没有返回统一认证错误码")
    if invalid_error.get("retryable") is not False or not invalid_error.get("request_id"):
        raise RuntimeError("错误响应缺少 request_id 或 retryable")
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

        assert_error(
            client,
            "POST",
            "/api/v1/documents",
            403,
            "CSRF_ORIGIN_INVALID",
            headers={**web_headers, "Origin": "https://not-allowed.example", "Idempotency-Key": "origin-check"},
            json={
                "document_type": "resume",
                "subject_type": "self_resume",
                "title": "不应创建",
                "text": "这条请求只用于验证 Origin。",
            },
        )

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
        file_impact_response, file_impact_body = call(client, "GET", f"/api/v1/documents/{file_document['id']}/deletion-impact", headers=web_headers)
        file_impact_etag = file_impact_response.headers.get("ETag")
        if not data_of(file_impact_body).get("impact_version") or not file_impact_etag:
            raise RuntimeError("文件资料删除影响快照无效")
        call(client, "DELETE", f"/api/v1/documents/{file_document['id']}", expected=(204,), headers={**web_headers, "If-Match": file_impact_etag})
        if client.get(f"{BASE_URL}/api/v1/documents/{file_document['id']}/file", headers=web_headers).status_code != 404:
            raise RuntimeError("删除资料后原始文件仍可访问")

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
        report_payload = report.get("report") or {}
        if not report_payload.get("dimensions"):
            raise RuntimeError("报告没有能力维度和证据结果")
        if not report_payload.get("verification_items"):
            raise RuntimeError("正式报告没有待核实事项")
        if not report_payload.get("interview_questions"):
            raise RuntimeError("正式报告没有针对性面试问题")
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
        # 覆盖岗位版编辑保存这条回归链路：内部岗位主键不能被误当成 public_id。
        initial_version = (variant.get("versions") or [])[0]
        _, saved_variant_body = call(
            client,
            "POST",
            f"/api/v1/resumes/{variant['id']}/versions",
            expected=(201,),
            headers={**web_headers, "Idempotency-Key": f"variant-version-{suffix}"},
            json={
                "base_revision": variant["revision"],
                "content": initial_version["content"],
                "layout": initial_version["layout"],
                "template_version": initial_version["template_version"],
            },
        )
        saved_variant = data_of(saved_variant_body)
        if saved_variant.get("revision") != variant["revision"] + 1 or len(saved_variant.get("versions") or []) != 2:
            raise RuntimeError("岗位版简历保存没有生成新版本")
        _, saved_variant_repeat_body = call(
            client,
            "POST",
            f"/api/v1/resumes/{variant['id']}/versions",
            expected=(200,),
            headers={**web_headers, "Idempotency-Key": f"variant-version-{suffix}"},
            json={
                "base_revision": variant["revision"],
                "content": initial_version["content"],
                "layout": initial_version["layout"],
                "template_version": initial_version["template_version"],
            },
        )
        if data_of(saved_variant_repeat_body).get("revision") != saved_variant.get("revision"):
            raise RuntimeError("岗位版简历保存幂等重试改变了版本")
        variant_version = ((saved_variant.get("versions") or [None])[0] or {}).get("id")
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
            expected=(201,),
            headers={**web_headers, "Idempotency-Key": f"browser-code-{suffix}"},
            json={"origin": "https://www.zhipin.com", "nonce": browser_nonce},
        )
        browser_code = data_of(browser_code_body)["authorization_code"]
        _, exchange_body = call(
            client,
            "POST",
            "/api/v1/browser-auth/exchange",
            expected=(201,),
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

        _, full_interview_body = call(
            client,
            "POST",
            "/api/v1/interviews",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"interview-full-{suffix}"},
            json={
                "job_pool_item_id": pool["id"],
                "analysis_id": analysis["id"],
                "resume_document_version_id": resume_version["id"],
                "title": "201 完整面试练习",
                "confirm_usage": True,
            },
        )
        full_interview = data_of(full_interview_body)["interview"]
        if len(full_interview.get("questions") or []) != 3 or full_interview.get("status") != "awaiting_answer":
            raise RuntimeError("面试没有恢复为 3 个主问题的等待回答状态")
        if full_interview.get("current_question_id") != full_interview["questions"][0]["id"]:
            raise RuntimeError("面试没有返回当前可回答的第一道主问题")
        _, interview_resume_body = call(client, "GET", f"/api/v1/interviews/{full_interview['id']}", headers=web_headers)
        if data_of(interview_resume_body).get("id") != full_interview.get("id"):
            raise RuntimeError("刷新面试详情没有恢复原会话")
        first_question = full_interview["questions"][0]
        _, full_after_first_body = call(
            client,
            "POST",
            f"/api/v1/interviews/{full_interview['id']}/answers",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"answer-full-first-{suffix}"},
            json={
                "question_id": first_question["id"],
                # 短回答固定触发一次追问，验证客户端不能按数组顺序猜当前题目。
                "answer_text": "参与过开发。",
                "base_revision": full_interview["revision"],
            },
        )
        full_after_first = data_of(full_after_first_body)["interview"]
        followups = [question for question in full_after_first.get("questions") or [] if question.get("question_type") == "followup"]
        if full_after_first.get("status") != "awaiting_answer" or len(followups) != 1:
            raise RuntimeError("首题短回答没有生成唯一追问")
        followup = followups[0]
        if full_after_first.get("current_question_id") != followup.get("id"):
            raise RuntimeError("面试追问没有成为当前可回答题目")

        _, full_after_followup_body = call(
            client,
            "POST",
            f"/api/v1/interviews/{full_interview['id']}/answers",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"answer-full-followup-{suffix}"},
            json={
                "question_id": followup["id"],
                "answer_text": "我补充说明本人负责接口拆分、组件实现和上线验证，最终把页面性能问题定位并修复。",
                "base_revision": full_after_first["revision"],
            },
        )
        full_after_followup = data_of(full_after_followup_body)["interview"]
        main_two = next((question for question in full_after_followup.get("questions") or [] if question.get("main_no") == 2 and question.get("question_type") == "main"), None)
        if full_after_followup.get("current_question_id") != (main_two or {}).get("id"):
            raise RuntimeError("追问回答后没有进入第二道主问题")

        _, full_after_second_body = call(
            client,
            "POST",
            f"/api/v1/interviews/{full_interview['id']}/answers",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"answer-full-second-{suffix}"},
            json={
                "question_id": main_two["id"],
                "answer_text": "我负责需求拆解、组件实现和上线验证，并通过自动化测试保证交付质量。",
                "base_revision": full_after_followup["revision"],
            },
        )
        full_after_second = data_of(full_after_second_body)["interview"]
        main_three = next((question for question in full_after_second.get("questions") or [] if question.get("main_no") == 3 and question.get("question_type") == "main"), None)
        if full_after_second.get("current_question_id") != (main_three or {}).get("id"):
            raise RuntimeError("第二道主问题回答后没有进入第三道主问题")

        _, full_finished_body = call(
            client,
            "POST",
            f"/api/v1/interviews/{full_interview['id']}/answers",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"answer-full-third-{suffix}"},
            json={
                "question_id": main_three["id"],
                "answer_text": "我结合业务目标完成方案设计、代码开发和发布复盘，结果是交付过程更稳定。",
                "base_revision": full_after_second["revision"],
            },
        )
        full_finished = data_of(full_finished_body)["interview"]
        full_summary = full_finished.get("summary") or {}
        if full_finished.get("status") != "completed" or full_summary.get("completion_type") != "full":
            raise RuntimeError("面试完成全部主问题后没有生成 full 总结")
        if full_summary.get("content", {}).get("answered_main_count") != 3 or full_summary.get("content", {}).get("answered_followup_count") != 1:
            raise RuntimeError("full 总结没有统计 3 道主问题和 1 道追问")

        # 另开一场会话保留提前结束分支，确认未完成练习仍能生成 early 总结。
        _, early_interview_body = call(
            client,
            "POST",
            "/api/v1/interviews",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"interview-early-{suffix}"},
            json={
                "job_pool_item_id": pool["id"],
                "analysis_id": analysis["id"],
                "resume_document_version_id": resume_version["id"],
                "title": "201 提前结束面试练习",
                "confirm_usage": True,
            },
        )
        early_interview = data_of(early_interview_body)["interview"]
        early_first_question = early_interview["questions"][0]
        _, early_after_answer_body = call(
            client,
            "POST",
            f"/api/v1/interviews/{early_interview['id']}/answers",
            expected=(202,),
            headers={**web_headers, "Idempotency-Key": f"answer-early-{suffix}"},
            json={
                "question_id": early_first_question["id"],
                "answer_text": "我负责需求拆解、组件实现和上线验证，并通过性能优化解决了实际问题。",
                "base_revision": early_interview["revision"],
            },
        )
        early_after_answer = data_of(early_after_answer_body)["interview"]
        _, early_finish_body = call(
            client,
            "POST",
            f"/api/v1/interviews/{early_interview['id']}/finish",
            expected=(200,),
            headers={**web_headers, "Idempotency-Key": f"finish-early-{suffix}"},
            json={"base_revision": early_after_answer["revision"]},
        )
        early_finished = data_of(early_finish_body)
        if early_finished.get("status") != "ended_early" or (early_finished.get("summary") or {}).get("completion_type") != "early":
            raise RuntimeError("提前结束面试没有生成 early 总结")
        interview_ids = (full_interview["id"], early_interview["id"])

        # 用独立 HTTP 客户端验证跨账号隔离，不覆盖主验收账号的 Cookie。
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
                "title": "待删除验收资料",
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

        # 删除已被分析链路引用的简历，验证影响集合中的派生资源同时撤销访问。
        cascade_impact_response, cascade_impact_body = call(
            client,
            "GET",
            f"/api/v1/documents/{resume['id']}/deletion-impact",
            headers=web_headers,
        )
        cascade_impact = data_of(cascade_impact_body)
        affected = cascade_impact.get("affected") or {}
        if int(affected.get("versions") or 0) < 1 or int(affected.get("analyses") or 0) < 1:
            raise RuntimeError("级联删除影响快照没有识别简历版本和分析报告")
        cascade_etag = cascade_impact_response.headers.get("ETag")
        if not cascade_etag:
            raise RuntimeError("级联删除影响快照没有返回 ETag")
        call(
            client,
            "DELETE",
            f"/api/v1/documents/{resume['id']}",
            expected=(204,),
            headers={**web_headers, "If-Match": cascade_etag},
        )
        for resource_path in (
            f"/api/v1/documents/{resume['id']}",
            f"/api/v1/analyses/{analysis['id']}",
            f"/api/v1/rewrites/{rewrite['id']}",
            f"/api/v1/resumes/{variant['id']}",
        ) + tuple(f"/api/v1/interviews/{interview_id}" for interview_id in interview_ids):
            assert_error(client, "GET", resource_path, 404, "RESOURCE_NOT_FOUND", headers=web_headers)
        _, expired_export_body = call(client, "GET", f"/api/v1/exports/{export['id']}", headers=web_headers)
        expired_export = data_of(expired_export_body)
        if expired_export.get("status") != "expired" or expired_export.get("file_available"):
            raise RuntimeError("级联删除没有把导出标为过期")
        assert_error(
            client,
            "GET",
            f"/api/v1/exports/{export['id']}/file",
            404,
            "RESOURCE_NOT_FOUND",
            headers=web_headers,
        )
        call(
            client,
            "POST",
            "/api/v1/auth/logout",
            expected=(204,),
            headers={**web_headers, "Origin": "http://127.0.0.1:8001"},
        )

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
                "interview_followup_current": True,
                "interview_full_summary": full_summary.get("completion_type"),
                "interview_early_summary": (early_finished.get("summary") or {}).get("completion_type"),
                "cross_account_isolation": True,
                "delete_old_entry_blocked": True,
                "cascade_delete_access_revoked": True,
                "auth_contract": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
