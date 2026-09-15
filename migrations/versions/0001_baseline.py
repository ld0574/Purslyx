"""建立当前 Purslyx 业务模型的 PostgreSQL 基线。"""

from __future__ import annotations

from alembic import op

from server.app import models  # noqa: F401  # 注册全部模型
from server.app.db import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 201 PostgreSQL 环境的首个版本以 ORM 元数据建立完整基线；后续变更使用显式迁移。
    # 该操作是幂等的，不删除既有表和数据。
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    # 演示库禁止通过迁移链路批量删除业务表，回滚由数据库备份流程负责。
    pass
