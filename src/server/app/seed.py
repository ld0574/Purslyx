"""固定权限、角色和本地开发账号的种子数据。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    Account,
    AccountRole,
    AdminPermission,
    AdminRole,
    ModelPriceVersion,
    RolePermission,
)
from .security import hash_password, normalize_email

PERMISSIONS = [
    ("admin.users.read", "查看用户", "查看账号概要和使用概况"),
    ("admin.users.manage_status", "管理用户状态", "暂停或恢复账号"),
    ("admin.roles.manage", "管理角色权限", "维护角色和权限组合"),
    ("admin.usage.grant", "发放使用次数", "给适用账号追加次数"),
    ("admin.stats.read", "查看站点概况", "查看聚合统计、成本和反馈"),
    ("admin.job_pool.read", "查看抓取岗位", "跨账号查看抓取 JD、来源和匹配状态"),
    ("admin.job_pool.manage", "管理抓取岗位", "下架抓取岗位并取消关联任务"),
    ("admin.logs.operations.read", "查看管理日志", "查看管理操作审计"),
    ("admin.logs.security.read", "查看安全日志", "查看登录和会话安全事件"),
    ("admin.logs.tasks.read", "查看任务日志", "查看任务执行摘要"),
    ("admin.logs.export", "导出日志", "申请和下载受限日志导出"),
]


def seed_permissions(db: Session) -> None:
    """幂等地创建权限目录和内置超级管理员角色。"""

    permission_map: dict[str, AdminPermission] = {}
    for key, display_name, description in PERMISSIONS:
        item = db.scalar(select(AdminPermission).where(AdminPermission.key == key))
        if item is None:
            item = AdminPermission(key=key, display_name=display_name, description=description)
            db.add(item)
            db.flush()
        permission_map[key] = item

    role = db.scalar(select(AdminRole).where(AdminRole.name == "超级管理员"))
    if role is None:
        role = AdminRole(
            name="超级管理员",
            description="本地初始化的全量管理角色",
            is_builtin=True,
            status="active",
        )
        db.add(role)
        db.flush()

    existing = {
        row.permission_id
        for row in db.scalars(select(RolePermission).where(RolePermission.role_id == role.id)).all()
    }
    for permission in permission_map.values():
        if permission.id not in existing:
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))

    # 只有部署者显式注入管理员邮箱和密码时才创建管理员，不在代码中放默认凭据。
    if settings.admin_email and settings.admin_password:
        account = db.scalar(select(Account).where(Account.email_normalized == normalize_email(settings.admin_email)))
        if account is None:
            account = Account(
                email=settings.admin_email.strip(),
                email_normalized=normalize_email(settings.admin_email),
                password_hash=hash_password(settings.admin_password),
                registration_role="recruiter",
                status="active",
                email_verified_at=datetime.now(timezone.utc),
            )
            db.add(account)
            db.flush()
        if db.scalar(select(AccountRole).where(AccountRole.account_id == account.id, AccountRole.role_id == role.id)) is None:
            db.add(AccountRole(account_id=account.id, role_id=role.id))

    seed_model_prices(db)


def seed_model_prices(db: Session) -> None:
    """种入本地零成本价格；外部模型只有显式配置价格才允许调用。"""

    provider = settings.model_provider.strip().lower()
    values = {
        "input": settings.model_input_usd_per_million,
        "cached": settings.model_cached_input_usd_per_million,
        "output": settings.model_output_usd_per_million,
    }
    if provider == "local":
        values = {"input": "0", "cached": "0", "output": "0"}
    if any(not value.strip() for value in values.values()):
        return
    try:
        parsed = {key: Decimal(value) for key, value in values.items()}
    except (InvalidOperation, ValueError):
        return
    if any(value < 0 or not value.is_finite() for value in parsed.values()):
        return
    current = datetime.now(timezone.utc)
    existing = db.scalar(
        select(ModelPriceVersion).where(
            ModelPriceVersion.provider == provider,
            ModelPriceVersion.model == settings.model_name,
            ModelPriceVersion.effective_to.is_(None),
        )
    )
    if existing is None:
        db.add(
            ModelPriceVersion(
                provider=provider,
                model=settings.model_name,
                input_usd_per_million=parsed["input"],
                cached_input_usd_per_million=parsed["cached"],
                output_usd_per_million=parsed["output"],
                source_url="local://configured-runtime-price" if provider == "local" else "runtime-config",
                verified_on=current.date().isoformat(),
                effective_from=current,
            )
        )
