"""把 201 运行库显式迁移到当前 ORM 契约。"""

from __future__ import annotations

from alembic import op

revision = "0004_align_runtime_schema"
down_revision = "0003_job_pool_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """补齐索引和精确金额类型，并安全移除已经清空的旧 URL 列。"""

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_admin_audit_events_idempotency_key
          ON admin_audit_events(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_analysis_reports_preference_version_id
          ON analysis_reports(preference_version_id);
        CREATE INDEX IF NOT EXISTS ix_apply_click_events_metric_date
          ON apply_click_events(metric_date);
        CREATE INDEX IF NOT EXISTS ix_browser_job_drafts_idempotency_key
          ON browser_job_drafts(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_document_versions_idempotency_key
          ON document_versions(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_documents_idempotency_key
          ON documents(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_job_pool_items_idempotency_key
          ON job_pool_items(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_job_preference_versions_idempotency_key
          ON job_preference_versions(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_job_preferences_idempotency_key
          ON job_preferences(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_log_exports_idempotency_key
          ON log_exports(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_product_feedback_idempotency_key
          ON product_feedback(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_product_feedback_reviewed_by_account_id
          ON product_feedback(reviewed_by_account_id);
        CREATE INDEX IF NOT EXISTS ix_resume_fact_versions_idempotency_key
          ON resume_fact_versions(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_resume_facts_idempotency_key
          ON resume_facts(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_resume_segment_decisions_idempotency_key
          ON resume_segment_decisions(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_resume_variant_versions_idempotency_key
          ON resume_variant_versions(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_resume_variant_versions_rewrite_id
          ON resume_variant_versions(rewrite_id);
        CREATE INDEX IF NOT EXISTS ix_resume_variants_idempotency_key
          ON resume_variants(idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_task_outbox_claimed_by
          ON task_outbox(claimed_by);

        DO $$
        BEGIN
          IF to_regclass('public.ix_budget_reservations_call_key') IS NULL
             AND to_regclass('public.uk_budget_reservation_call_key') IS NOT NULL
          THEN
            ALTER INDEX uk_budget_reservation_call_key
              RENAME TO ix_budget_reservations_call_key;
          END IF;
        END $$;
        DROP INDEX IF EXISTS uk_budget_reservation_call_key;
        CREATE UNIQUE INDEX IF NOT EXISTS ix_budget_reservations_call_key
          ON budget_reservations(call_key);

        ALTER TABLE model_price_versions
          ALTER COLUMN input_usd_per_million TYPE NUMERIC(20,8)
            USING input_usd_per_million::numeric(20,8),
          ALTER COLUMN cached_input_usd_per_million TYPE NUMERIC(20,8)
            USING cached_input_usd_per_million::numeric(20,8),
          ALTER COLUMN output_usd_per_million TYPE NUMERIC(20,8)
            USING output_usd_per_million::numeric(20,8);

        UPDATE product_feedback SET revision = 1 WHERE revision IS NULL;
        ALTER TABLE product_feedback ALTER COLUMN revision SET NOT NULL;

        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'apply_click_events'
              AND column_name = 'target_url'
          ) THEN
            IF EXISTS (SELECT 1 FROM apply_click_events WHERE target_url IS NOT NULL) THEN
              RAISE EXCEPTION '旧 target_url 仍有数据，需先核对并清理后再迁移';
            END IF;
            ALTER TABLE apply_click_events DROP COLUMN target_url;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """恢复旧结构形态；已清理的旧 URL 内容不会被伪造。"""

    op.execute(
        """
        ALTER TABLE apply_click_events ADD COLUMN IF NOT EXISTS target_url VARCHAR(1024);
        ALTER TABLE product_feedback ALTER COLUMN revision DROP NOT NULL;
        ALTER TABLE model_price_versions
          ALTER COLUMN input_usd_per_million TYPE DOUBLE PRECISION
            USING input_usd_per_million::double precision,
          ALTER COLUMN cached_input_usd_per_million TYPE DOUBLE PRECISION
            USING cached_input_usd_per_million::double precision,
          ALTER COLUMN output_usd_per_million TYPE DOUBLE PRECISION
            USING output_usd_per_million::double precision;

        DROP INDEX IF EXISTS ix_admin_audit_events_idempotency_key;
        DROP INDEX IF EXISTS ix_analysis_reports_preference_version_id;
        DROP INDEX IF EXISTS ix_apply_click_events_metric_date;
        DROP INDEX IF EXISTS ix_browser_job_drafts_idempotency_key;
        DROP INDEX IF EXISTS ix_document_versions_idempotency_key;
        DROP INDEX IF EXISTS ix_documents_idempotency_key;
        DROP INDEX IF EXISTS ix_job_pool_items_idempotency_key;
        DROP INDEX IF EXISTS ix_job_preference_versions_idempotency_key;
        DROP INDEX IF EXISTS ix_job_preferences_idempotency_key;
        DROP INDEX IF EXISTS ix_log_exports_idempotency_key;
        DROP INDEX IF EXISTS ix_product_feedback_idempotency_key;
        DROP INDEX IF EXISTS ix_product_feedback_reviewed_by_account_id;
        DROP INDEX IF EXISTS ix_resume_fact_versions_idempotency_key;
        DROP INDEX IF EXISTS ix_resume_facts_idempotency_key;
        DROP INDEX IF EXISTS ix_resume_segment_decisions_idempotency_key;
        DROP INDEX IF EXISTS ix_resume_variant_versions_idempotency_key;
        DROP INDEX IF EXISTS ix_resume_variant_versions_rewrite_id;
        DROP INDEX IF EXISTS ix_resume_variants_idempotency_key;
        DROP INDEX IF EXISTS ix_task_outbox_claimed_by;

        DROP INDEX IF EXISTS ix_budget_reservations_call_key;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_budget_reservation_call_key
          ON budget_reservations(call_key) WHERE call_key IS NOT NULL;
        """
    )
