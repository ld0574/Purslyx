"""为管理操作和次数发放补数据库级幂等约束。"""

from __future__ import annotations

from alembic import op

revision = "0006_admin_idempotency"
down_revision = "0005_hide_deleted_resume_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """同一操作者动作或同一目标发放不能重复占用一个幂等键。"""

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uk_admin_audit_action_idempotency
          ON admin_audit_events(operator_account_id, action, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_usage_grant_account_idempotency
          ON usage_grants(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    """移除新增的数据库级幂等约束。"""

    op.execute(
        """
        DROP INDEX IF EXISTS uk_usage_grant_account_idempotency;
        DROP INDEX IF EXISTS uk_admin_audit_action_idempotency;
        """
    )
