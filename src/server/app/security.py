"""认证、令牌和权限辅助函数。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .errors import DomainError, NotFoundError
from .models import Account, AccountRole, AdminPermission, AdminRole, BrowserSession, WebSession


PASSWORD_HASHER = PasswordHasher()


def now_utc() -> datetime:
    """统一返回带时区的当前时间。"""

    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    """规范化邮箱，避免同一邮箱因大小写产生多个账号。"""

    return email.strip().casefold()


def hash_secret(value: str) -> str:
    """对随机令牌做不可逆摘要；随机令牌本身只在签发时返回。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compare_secret(value: str, digest: str) -> bool:
    return hmac.compare_digest(hash_secret(value), digest)


def issue_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise DomainError("AUTH_PASSWORD_INVALID", "密码长度需为 12 到 128 个字符", 422)
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def session_expiry(days: int) -> datetime:
    return now_utc() + timedelta(days=days)


def browser_expiry(hours: int) -> datetime:
    return now_utc() + timedelta(hours=hours)


def _web_token(request: Request, authorization: str | None) -> str | None:
    cookie = request.cookies.get("purslyx_session")
    if cookie:
        return cookie
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_web_account(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> Account:
    """从 Cookie 或 Bearer 读取 Web 会话，并实时校验账号状态。"""

    raw_token = _web_token(request, authorization)
    if not raw_token:
        raise DomainError("AUTH_SESSION_EXPIRED", "请先登录", 401, "login")
    session = db.scalar(
        select(WebSession).where(WebSession.token_hash == hash_secret(raw_token))
    )
    if session is None or session.revoked_at is not None or session.expires_at <= now_utc():
        raise DomainError("AUTH_SESSION_EXPIRED", "登录已失效，请重新登录", 401, "login")
    account = db.get(Account, session.account_id)
    if account is None:
        raise DomainError("AUTH_SESSION_EXPIRED", "登录已失效，请重新登录", 401, "login")
    if account.status == "suspended":
        raise DomainError("AUTH_ACCOUNT_SUSPENDED", "账号已暂停", 403)
    request.state.web_session = session
    return account


def require_csrf(request: Request, account: Account) -> None:
    """写请求检查 CSRF，Bearer 浏览器会话不绕过该检查。"""

    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    session: WebSession | None = getattr(request.state, "web_session", None)
    if session is None:
        return
    supplied = request.headers.get("X-CSRF-Token") or request.query_params.get("csrf_token")
    if not supplied or not compare_secret(supplied, session.csrf_token_hash):
        raise DomainError("CSRF_INVALID", "请求校验已失效，请刷新页面后重试", 403, "refresh")


def browser_account(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> Account:
    """读取受限 Browser Bearer 会话，只用于岗位草稿接口。"""

    if not authorization or not authorization.lower().startswith("bearer "):
        raise DomainError("AUTH_SESSION_EXPIRED", "浏览器授权已失效", 401)
    raw_token = authorization[7:].strip()
    session = db.scalar(
        select(BrowserSession).where(BrowserSession.token_hash == hash_secret(raw_token))
    )
    if session is None or session.revoked_at is not None or session.expires_at <= now_utc():
        raise DomainError("AUTH_SESSION_EXPIRED", "浏览器授权已失效", 401)
    account = db.get(Account, session.account_id)
    if account is None or account.status != "active":
        raise DomainError("AUTH_SESSION_EXPIRED", "浏览器授权已失效", 401)
    request.state.browser_session = session
    return account


def permissions_for(db: Session, account_id: int) -> set[str]:
    """读取当前后台权限，不使用客户端传入的角色。"""

    rows = db.execute(
        select(AdminPermission.key)
        .join(RolePermission, RolePermission.permission_id == AdminPermission.id)
        .join(AdminRole, AdminRole.id == RolePermission.role_id)
        .join(AccountRole, AccountRole.role_id == AdminRole.id)
        .where(
            AccountRole.account_id == account_id,
            AdminRole.status == "active",
        )
    ).all()
    return {row[0] for row in rows}


def require_permission(db: Session, account: Account, permission: str) -> None:
    if permission not in permissions_for(db, account.id):
        raise DomainError("ADMIN_PERMISSION_DENIED", "当前账号没有该管理权限", 403)


def public_account(db: Session, account: Account) -> dict:
    return {
        "id": account.public_id,
        "email": account.email,
        "registration_role": account.registration_role,
        "status": account.status,
        "email_verified": account.email_verified_at is not None,
        "admin_permissions": sorted(permissions_for(db, account.id)),
        "created_at": account.created_at.isoformat(),
        "last_login_at": account.last_login_at.isoformat() if account.last_login_at else None,
        "revision": account.revision,
    }


def require_seeker(account: Account) -> None:
    if account.registration_role != "seeker":
        raise DomainError("ROLE_NOT_ALLOWED", "此功能仅对求职身份开放", 403)


def require_recruiter(account: Account) -> None:
    if account.registration_role != "recruiter":
        raise DomainError("ROLE_NOT_ALLOWED", "此功能仅对招聘身份开放", 403)


def require_verified(account: Account) -> None:
    if account.email_verified_at is None:
        raise DomainError("AUTH_EMAIL_UNVERIFIED", "请先完成邮箱验证", 403, "verify_email")
