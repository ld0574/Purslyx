"""为岗位池补齐账号级幂等字段，避免重复提交产生重复岗位。"""

from __future__ import annotations

from alembic import op


revision = "0003_job_pool_idempotency"
down_revision = "0002_current_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('public.job_pool_items') IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='job_pool_items' AND column_name='idempotency_key'
             )
          THEN ALTER TABLE job_pool_items ADD COLUMN idempotency_key VARCHAR(128); END IF;
          IF to_regclass('public.job_pool_items') IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='job_pool_items' AND column_name='request_hash'
             )
          THEN ALTER TABLE job_pool_items ADD COLUMN request_hash VARCHAR(64); END IF;
        END $$;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_job_pool_account_idempotency
          ON job_pool_items(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    # 201 演示库不通过回滚删除幂等事实。
    pass
