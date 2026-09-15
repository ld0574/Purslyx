#!/usr/bin/env python3
"""验证 201 PostgreSQL 上授权管理端的完整成功与拒绝链路。"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from migrate_201 import configure_environment

BASE_URL = "http://127.0.0.1:8001"


def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{response.request.method} {response.request.url.path} 返回非 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("接口响应不是对象")
    return value


def _expect(response: httpx.Response, *statuses: int) -> dict[str, Any]:
    if response.status_code not in statuses:
        error = (_body(response).get("error") or {}).get("code", "UNKNOWN")
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} 返回 {response.status_code}/{error}，"
            f"期望 {statuses}"
        )
    return _body(response) if response.status_code not in {204, 304} else {}


def _data(response: httpx.Response, *statuses: int) -> Any:
    body = _expect(response, *statuses)
    if "data" not in body:
        raise RuntimeError("成功响应缺少 data")
    return body["data"]


def _expect_error(response: httpx.Response, status: int, code: str) -> None:
    body = _expect(response, status)
    actual = (body.get("error") or {}).get("code")
    if actual != code:
        raise RuntimeError(f"错误码为 {actual!r}，期望 {code!r}")


def _login(client: httpx.Client, email: str, password: str) -> tuple[dict[str, str], dict[str, Any]]:
    data = _data(
        client.post(f"{BASE_URL}/api/v1/auth/login", json={"email": email, "password": password}),
        200,
    )
    headers = {
        "Authorization": f"Bearer {data['access_token']}",
        "X-CSRF-Token": data["csrf_token"],
    }
    return headers, data["account"]


def _write_headers(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


def _bootstrap_accounts(suffix: str, password: str) -> tuple[str, str]:
    """直接创建随机验收账号；不使用或输出任何真实管理员凭据。"""

    configure_environment()
    from sqlalchemy import select

    from server.app.db import session_scope
    from server.app.models import Account, AccountRole, AdminRole
    from server.app.security import hash_password, normalize_email
    from server.app.services import grant_trial_if_needed

    admin_email = f"admin-smoke-{suffix}@purslyx.local"
    target_email = f"admin-target-{suffix}@purslyx.local"
    with session_scope() as db:
        super_role = db.scalar(
            select(AdminRole).where(
                AdminRole.name == "超级管理员",
                AdminRole.is_builtin.is_(True),
                AdminRole.status == "active",
            )
        )
        if super_role is None:
            raise RuntimeError("201 数据库缺少内置超级管理员角色")
        admin = Account(
            email=admin_email,
            email_normalized=normalize_email(admin_email),
            password_hash=hash_password(password),
            registration_role="recruiter",
            status="active",
            email_verified_at=datetime.now(timezone.utc),
        )
        target = Account(
            email=target_email,
            email_normalized=normalize_email(target_email),
            password_hash=hash_password(password),
            registration_role="seeker",
            status="active",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add_all([admin, target])
        db.flush()
        db.add(AccountRole(account_id=admin.id, role_id=super_role.id))
        grant_trial_if_needed(db, target)
    return admin_email, target_email


def main() -> None:
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    password = f"Purslyx-{secrets.token_urlsafe(18)}!"
    admin_email, target_email = _bootstrap_accounts(suffix, password)

    with httpx.Client(timeout=30, follow_redirects=False) as client:
        health = _expect(client.get(f"{BASE_URL}/health"), 200)
        if health.get("environment") != "201" or (health.get("database") or {}).get("backend") != "postgresql":
            raise RuntimeError("服务没有连接 201 PostgreSQL")

        target_headers, target_account = _login(client, target_email, password)
        feedback = _data(
            client.post(
                f"{BASE_URL}/api/v1/feedback",
                headers=_write_headers(target_headers, f"feedback-{suffix}"),
                json={"feedback_type": "suggestion", "content": "管理员完整链路验收反馈", "rating": 5},
            ),
            201,
        )
        # 产生一条当前目标账号的任务运行日志，便于验证三类日志和详情。
        _data(
            client.post(
                f"{BASE_URL}/api/v1/documents",
                headers=_write_headers(target_headers, f"document-{suffix}"),
                json={
                    "document_type": "resume",
                    "subject_type": "self_resume",
                    "title": "管理员验收任务资料",
                    "text": "负责 PostgreSQL 服务开发与自动化测试。",
                },
            ),
            201,
        )

        admin_headers, admin_account = _login(client, admin_email, password)
        required_permissions = {
            "admin.users.read",
            "admin.users.manage_status",
            "admin.roles.manage",
            "admin.usage.grant",
            "admin.stats.read",
            "admin.logs.operations.read",
            "admin.logs.security.read",
            "admin.logs.tasks.read",
            "admin.logs.export",
        }
        if not required_permissions <= set(admin_account.get("admin_permissions") or []):
            raise RuntimeError("临时管理员没有得到完整权限目录")

        users = _data(
            client.get(f"{BASE_URL}/api/v1/admin/users", headers=admin_headers, params={"q": target_email}),
            200,
        )
        target_rows = [item for item in users["items"] if item["id"] == target_account["id"]]
        if len(target_rows) != 1:
            raise RuntimeError("管理员用户检索没有唯一找到目标账号")
        if not isinstance(target_rows[0].get("balances"), list) or "recent_activity_at" not in target_rows[0]:
            raise RuntimeError("管理员用户列表缺少用量或最近活动概要")
        detail = _data(
            client.get(f"{BASE_URL}/api/v1/admin/users/{target_account['id']}", headers=admin_headers),
            200,
        )
        if not isinstance(detail.get("summary", {}).get("counts", {}).get("tasks"), int):
            raise RuntimeError("管理员用户详情缺少资料与任务数量概要")

        roles_payload = _data(client.get(f"{BASE_URL}/api/v1/admin/roles", headers=admin_headers), 200)
        if len(roles_payload.get("permission_catalog") or []) != len(required_permissions):
            raise RuntimeError("角色页面没有返回完整权限目录")
        if not any(role.get("is_builtin") and role.get("member_count", 0) >= 1 for role in roles_payload["items"]):
            raise RuntimeError("内置超级管理员角色或成员统计不正确")

        role_key = f"role-create-{suffix}"
        role_payload = {
            "name": f"只读支持-{suffix}",
            "description": "管理员完整链路自动验收创建",
            "permission_keys": ["admin.users.read", "admin.logs.security.read"],
            "status": "active",
            "reason": "验证角色创建与权限边界",
        }
        role = _data(
            client.post(
                f"{BASE_URL}/api/v1/admin/roles",
                headers=_write_headers(admin_headers, role_key),
                json=role_payload,
            ),
            201,
        )
        replayed_role = _data(
            client.post(
                f"{BASE_URL}/api/v1/admin/roles",
                headers=_write_headers(admin_headers, role_key),
                json=role_payload,
            ),
            200,
        )
        if replayed_role["id"] != role["id"]:
            raise RuntimeError("角色创建幂等重放没有返回原角色")
        _expect_error(
            client.post(
                f"{BASE_URL}/api/v1/admin/roles",
                headers=_write_headers(admin_headers, role_key),
                json={**role_payload, "name": f"冲突角色-{suffix}"},
            ),
            409,
            "IDEMPOTENCY_CONFLICT",
        )

        update_key = f"role-update-{suffix}"
        updated_payload = {
            **role_payload,
            "description": "已验证 revision 和幂等的受限角色",
            "base_revision": role["revision"],
            "reason": "验证角色更新",
        }
        updated_role = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/roles/{role['id']}",
                headers=_write_headers(admin_headers, update_key),
                json=updated_payload,
            ),
            200,
        )
        replayed_update = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/roles/{role['id']}",
                headers=_write_headers(admin_headers, update_key),
                json=updated_payload,
            ),
            200,
        )
        if replayed_update["revision"] != updated_role["revision"]:
            raise RuntimeError("角色更新幂等重放改变了 revision")
        _expect_error(
            client.put(
                f"{BASE_URL}/api/v1/admin/roles/{role['id']}",
                headers=_write_headers(admin_headers, f"role-stale-{suffix}"),
                json={**updated_payload, "description": "过期 revision 请求"},
            ),
            409,
            "ADMIN_ROLE_REVISION_CONFLICT",
        )
        _expect_error(
            client.put(
                f"{BASE_URL}/api/v1/admin/users/{admin_account['id']}/roles",
                headers=_write_headers(admin_headers, f"self-role-{suffix}"),
                json={"role_ids": [], "base_revision": admin_account["revision"], "reason": "验证禁止自改角色"},
            ),
            409,
            "ADMIN_SELF_ROLE_CHANGE",
        )

        assignment_key = f"assign-role-{suffix}"
        assignment_payload = {
            "role_ids": [role["id"]],
            "base_revision": detail["account"]["revision"],
            "reason": "验证受限后台角色",
        }
        assigned = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/roles",
                headers=_write_headers(admin_headers, assignment_key),
                json=assignment_payload,
            ),
            200,
        )
        replayed_assignment = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/roles",
                headers=_write_headers(admin_headers, assignment_key),
                json=assignment_payload,
            ),
            200,
        )
        if replayed_assignment["account"]["revision"] != assigned["account"]["revision"]:
            raise RuntimeError("角色分配幂等重放改变了账号 revision")

        restricted_headers, restricted_account = _login(client, target_email, password)
        if set(restricted_account.get("admin_permissions") or []) != {
            "admin.users.read",
            "admin.logs.security.read",
        }:
            raise RuntimeError("目标账号没有得到精确的受限权限组合")
        _data(client.get(f"{BASE_URL}/api/v1/admin/users", headers=restricted_headers), 200)
        _expect_error(
            client.get(f"{BASE_URL}/api/v1/admin/roles", headers=restricted_headers),
            403,
            "ADMIN_PERMISSION_DENIED",
        )

        current_detail = _data(
            client.get(f"{BASE_URL}/api/v1/admin/users/{target_account['id']}", headers=admin_headers),
            200,
        )
        suspended = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/status",
                headers=_write_headers(admin_headers, f"suspend-{suffix}"),
                json={
                    "status": "suspended",
                    "reason": "验证暂停与会话撤销",
                    "base_revision": current_detail["account"]["revision"],
                },
            ),
            200,
        )
        suspended_replay = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/status",
                headers=_write_headers(admin_headers, f"suspend-{suffix}"),
                json={
                    "status": "suspended",
                    "reason": "验证暂停与会话撤销",
                    "base_revision": current_detail["account"]["revision"],
                },
            ),
            200,
        )
        if suspended_replay["account"]["revision"] != suspended["account"]["revision"]:
            raise RuntimeError("账号暂停幂等重放改变了 revision")
        revoked_response = client.get(f"{BASE_URL}/api/v1/me", headers=restricted_headers)
        if revoked_response.status_code != 401:
            raise RuntimeError("暂停账号后旧会话仍可访问")
        recovery = _data(
            client.post(
                f"{BASE_URL}/api/v1/auth/request-account-recovery",
                json={"email": target_email},
            ),
            202,
        )
        recovery_token = recovery.get("recovery_token")
        if not recovery_token:
            raise RuntimeError("暂停账号重新申请后没有生成本地恢复令牌")
        restored = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/status",
                headers=_write_headers(admin_headers, f"restore-{suffix}"),
                json={
                    "status": "active",
                    "reason": "完成暂停验收后恢复",
                    "base_revision": suspended["account"]["revision"],
                },
            ),
            200,
        )
        if restored["account"]["status"] != "active":
            raise RuntimeError("管理员没有恢复目标账号")
        _expect_error(
            client.post(
                f"{BASE_URL}/api/v1/auth/recover-account",
                json={"token": recovery_token},
            ),
            410,
            "AUTH_TOKEN_INVALID_OR_EXPIRED",
        )

        before_balance = next(
            item["available"] for item in current_detail["usage"]["balances"] if item["feature"] == "analysis"
        )
        grant_key = f"grant-{suffix}"
        grant_payload = {"feature": "analysis", "count": 3, "reason": "验证管理员次数追加幂等"}
        grant = _data(
            client.post(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/usage-grants",
                headers=_write_headers(admin_headers, grant_key),
                json=grant_payload,
            ),
            201,
        )
        repeated_grant = _data(
            client.post(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/usage-grants",
                headers=_write_headers(admin_headers, grant_key),
                json=grant_payload,
            ),
            200,
        )
        if grant["grant"]["id"] != repeated_grant["grant"]["id"] or grant["balance"]["available"] != before_balance + 3:
            raise RuntimeError("重复发放产生了新记录或余额增量不正确")
        _expect_error(
            client.post(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/usage-grants",
                headers=_write_headers(admin_headers, grant_key),
                json={**grant_payload, "count": 4},
            ),
            409,
            "USAGE_IDEMPOTENCY_CONFLICT",
        )

        metrics = _data(client.get(f"{BASE_URL}/api/v1/admin/metrics", headers=admin_headers), 200)
        costs = _data(client.get(f"{BASE_URL}/api/v1/admin/costs", headers=admin_headers), 200)
        if metrics.get("timezone") != "Asia/Shanghai" or costs.get("currency") != "USD":
            raise RuntimeError("管理统计或成本口径不完整")
        feedback_detail = _data(
            client.get(f"{BASE_URL}/api/v1/admin/feedback/{feedback['id']}", headers=admin_headers),
            200,
        )
        feedback_update_key = f"feedback-update-{suffix}"
        reviewed = _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/feedback/{feedback['id']}",
                headers=_write_headers(admin_headers, feedback_update_key),
                json={
                    "status": "reviewed",
                    "base_revision": feedback_detail["revision"],
                    "reason": "验证反馈处理审计",
                },
            ),
            200,
        )
        if reviewed["status"] != "reviewed":
            raise RuntimeError("反馈状态没有更新")

        log_counts: dict[str, int] = {}
        for log_type in ("operations", "security", "tasks"):
            logs = _data(
                client.get(
                    f"{BASE_URL}/api/v1/admin/logs/{log_type}",
                    headers=admin_headers,
                    params={"limit": 100},
                ),
                200,
            )
            items = logs.get("items") or []
            if not items:
                raise RuntimeError(f"{log_type} 日志没有返回当前 201 运行事实")
            detail_item = _data(
                client.get(
                    f"{BASE_URL}/api/v1/admin/logs/{log_type}/{items[0]['id']}",
                    headers=admin_headers,
                ),
                200,
            )
            if detail_item.get("category") != log_type:
                raise RuntimeError(f"{log_type} 日志详情分类不正确")
            log_counts[log_type] = len(items)

        export_key = f"log-export-{suffix}"
        log_export = _data(
            client.post(
                f"{BASE_URL}/api/v1/admin/log-exports",
                headers=_write_headers(admin_headers, export_key),
                json={"log_type": "operations", "export_format": "csv", "filters": {}},
            ),
            202,
        )
        if log_export["status"] != "downloadable":
            raise RuntimeError("内联日志导出没有进入可下载状态")
        downloaded = client.get(
            f"{BASE_URL}/api/v1/admin/log-exports/{log_export['id']}/file",
            headers=admin_headers,
        )
        if downloaded.status_code != 200 or not downloaded.content.startswith("\ufeff".encode("utf-8")):
            raise RuntimeError("日志 CSV 下载无效")

        # 清理授权状态：移除临时账号角色并归档临时自定义角色，业务验收记录仍保留审计。
        latest_target = _data(
            client.get(f"{BASE_URL}/api/v1/admin/users/{target_account['id']}", headers=admin_headers),
            200,
        )
        _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/users/{target_account['id']}/roles",
                headers=_write_headers(admin_headers, f"remove-role-{suffix}"),
                json={
                    "role_ids": [],
                    "base_revision": latest_target["account"]["revision"],
                    "reason": "完成验收后移除临时后台角色",
                },
            ),
            200,
        )
        _data(
            client.put(
                f"{BASE_URL}/api/v1/admin/roles/{role['id']}",
                headers=_write_headers(admin_headers, f"archive-role-{suffix}"),
                json={
                    **updated_payload,
                    "status": "archived",
                    "base_revision": updated_role["revision"],
                    "reason": "完成验收后归档临时角色",
                },
            ),
            200,
        )

        print(
            json.dumps(
                {
                    "environment": "201",
                    "database": "postgresql",
                    "user_search": True,
                    "role_permissions": True,
                    "role_idempotency": True,
                    "role_revision_conflict": True,
                    "self_escalation_denied": True,
                    "restricted_role_enforced": True,
                    "suspend_revokes_sessions": True,
                    "usage_grant_idempotency": True,
                    "metrics_and_costs": True,
                    "feedback_workflow": True,
                    "log_counts": log_counts,
                    "log_export_downloadable": True,
                    "result": "passed",
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
