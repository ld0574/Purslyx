"""固定权限、角色和本地演示账号的种子数据。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AdminPermission, AdminRole, AccountRole, RolePermission


PERMISSIONS = [
    ("admin.users.read", "查看用户", "查看账号概要和使用概况"),
    ("admin.users.manage_status", "管理用户状态", "暂停或恢复账号"),
    ("admin.roles.manage", "管理角色权限", "维护角色和权限组合"),
    ("admin.usage.grant", "发放使用次数", "给适用账号追加次数"),
    ("admin.stats.read", "查看站点概况", "查看聚合统计、成本和反馈"),
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
