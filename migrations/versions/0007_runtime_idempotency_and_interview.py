"""把业务写入幂等与面试轮次约束下沉到 PostgreSQL。"""

from __future__ import annotations

from alembic import op

revision = "0007_runtime_consistency"
down_revision = "0006_admin_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """并发请求也只能产生一份业务事实。"""

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uk_documents_account_idempotency
          ON documents(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_document_versions_account_idempotency
          ON document_versions(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_preferences_account_idempotency
          ON job_preferences(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_preference_versions_account_idempotency
          ON job_preference_versions(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_facts_account_idempotency
          ON resume_facts(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_fact_versions_account_idempotency
          ON resume_fact_versions(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_tasks_account_type_idempotency
          ON async_tasks(account_id, task_type, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_rewrite_decisions_account_idempotency
          ON resume_segment_decisions(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_resume_variants_account_idempotency
          ON resume_variants(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_variant_versions_account_idempotency
          ON resume_variant_versions(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_feedback_account_idempotency
          ON product_feedback(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_browser_drafts_account_idempotency
          ON browser_job_drafts(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_log_exports_account_idempotency
          ON log_exports(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_usage_grant_trial_batch
          ON usage_grants(account_id, feature, batch_key)
          WHERE batch_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_interview_answers_account_idempotency
          ON interview_answers(account_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uk_task_attempt_generation
          ON task_attempts(task_id, execution_generation);
        CREATE UNIQUE INDEX IF NOT EXISTS uk_interview_feedback_question
          ON interview_feedback(interview_id, question_id);
        CREATE UNIQUE INDEX IF NOT EXISTS uk_interview_summary
          ON interview_summaries(interview_id);
        """
    )


def downgrade() -> None:
    """仅移除本迁移新增的唯一索引。"""

    op.execute(
        """
        DROP INDEX IF EXISTS uk_interview_summary;
        DROP INDEX IF EXISTS uk_interview_feedback_question;
        DROP INDEX IF EXISTS uk_task_attempt_generation;
        DROP INDEX IF EXISTS uk_interview_answers_account_idempotency;
        DROP INDEX IF EXISTS uk_usage_grant_trial_batch;
        DROP INDEX IF EXISTS uk_log_exports_account_idempotency;
        DROP INDEX IF EXISTS uk_browser_drafts_account_idempotency;
        DROP INDEX IF EXISTS uk_feedback_account_idempotency;
        DROP INDEX IF EXISTS uk_variant_versions_account_idempotency;
        DROP INDEX IF EXISTS uk_resume_variants_account_idempotency;
        DROP INDEX IF EXISTS uk_rewrite_decisions_account_idempotency;
        DROP INDEX IF EXISTS uk_tasks_account_type_idempotency;
        DROP INDEX IF EXISTS uk_fact_versions_account_idempotency;
        DROP INDEX IF EXISTS uk_facts_account_idempotency;
        DROP INDEX IF EXISTS uk_preference_versions_account_idempotency;
        DROP INDEX IF EXISTS uk_preferences_account_idempotency;
        DROP INDEX IF EXISTS uk_document_versions_account_idempotency;
        DROP INDEX IF EXISTS uk_documents_account_idempotency;
        """
    )
