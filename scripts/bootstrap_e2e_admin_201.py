#!/usr/bin/env python3
"""为 201 浏览器 E2E 创建随机临时管理员，不输出测试凭据。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from migrate_201 import configure_environment


def main() -> None:
    email = os.getenv("PURSLYX_E2E_ADMIN_EMAIL", "").strip()
    password = os.getenv("PURSLYX_E2E_ADMIN_PASSWORD", "")
    if not email.endswith("@purslyx.local") or not email.startswith("e2e-admin-"):
        raise SystemExit("E2E 管理员必须使用随机 purslyx.local 地址")
    if len(password) < 12:
        raise SystemExit("E2E 管理员密码不符合长度要求")

    configure_environment()
    from sqlalchemy import select

    from server.app.db import session_scope
    from server.app.models import Account, AccountRole, AdminRole
    from server.app.security import hash_password, normalize_email

    with session_scope() as db:
        role = db.scalar(
            select(AdminRole).where(
                AdminRole.name == "超级管理员",
                AdminRole.is_builtin.is_(True),
                AdminRole.status == "active",
            )
        )
        if role is None:
            raise RuntimeError("201 数据库缺少内置超级管理员角色")
        account = Account(
            email=email,
            email_normalized=normalize_email(email),
            password_hash=hash_password(password),
            registration_role="recruiter",
            status="active",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(account)
        db.flush()
        db.add(AccountRole(account_id=account.id, role_id=role.id))
    print(json.dumps({"environment": "201", "database": "postgresql", "created": True}))


if __name__ == "__main__":
    main()
