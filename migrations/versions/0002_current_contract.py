"""为早期 201 演示库补齐当前任务、预算和分析字段。"""

from __future__ import annotations

from alembic import op

revision = "0002_current_contract"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 允许在已经由第一版 create_all 建好的 201 库上重复执行；正式生产环境可将
    # 同等语句拆成带锁评估的独立迁移。
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('public.analysis_reports') IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='analysis_reports' AND column_name='preference_version_id')
          THEN ALTER TABLE analysis_reports ADD COLUMN preference_version_id INTEGER; END IF;
          IF to_regclass('public.task_outbox') IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='task_outbox' AND column_name='claimed_by')
          THEN ALTER TABLE task_outbox ADD COLUMN claimed_by VARCHAR(120); END IF;
          IF to_regclass('public.task_outbox') IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='task_outbox' AND column_name='claimed_at')
          THEN ALTER TABLE task_outbox ADD COLUMN claimed_at TIMESTAMPTZ; END IF;
          IF to_regclass('public.budget_reservations') IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='budget_reservations' AND column_name='call_key')
          THEN ALTER TABLE budget_reservations ADD COLUMN call_key VARCHAR(160); END IF;
        END $$;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_budget_reservation_call_key
          ON budget_reservations(call_key) WHERE call_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    # 不删除线上演示库字段，避免回滚破坏正在使用的任务和预算事实。
    pass
