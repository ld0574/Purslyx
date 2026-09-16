"""为核心业务状态和数值边界补充数据库级约束。"""

from __future__ import annotations

from alembic import op

revision = "0009_database_checks"
down_revision = "0008_preference_source"
branch_labels = None
depends_on = None


# 迁移必须自包含，不能在历史迁移中导入会继续变化的 ORM 元数据。
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("account_tokens", "ck_account_tokens_type", "token_type IN ('verify_email', 'reset_password', 'recover_account')"),
    ("accounts", "ck_accounts_registration_role", "registration_role IN ('seeker', 'recruiter')"),
    ("accounts", "ck_accounts_revision", "revision >= 1"),
    ("accounts", "ck_accounts_status", "status IN ('pending_verification', 'active', 'suspended')"),
    ("admin_roles", "ck_admin_roles_status", "status IN ('active', 'archived')"),
    ("admin_roles", "ck_admin_roles_revision", "revision >= 1"),
    ("analysis_condition_results", "ck_analysis_conditions_status", "result_status IN ('matched', 'conflicted', 'unknown', 'unrestricted')"),
    ("analysis_condition_results", "ck_analysis_conditions_code", "condition_code IN ('job_title', 'location', 'work_mode', 'salary')"),
    ("analysis_condition_results", "ck_analysis_conditions_strength", "strength IS NULL OR strength IN ('prefer', 'important', 'required', 'negotiable')"),
    ("analysis_dimension_scores", "ck_analysis_dimensions_effective_weight", "effective_weight BETWEEN 0 AND 1"),
    ("analysis_dimension_scores", "ck_analysis_dimensions_base_weight", "base_weight BETWEEN 0 AND 1"),
    ("analysis_dimension_scores", "ck_analysis_dimensions_score", "score IS NULL OR score BETWEEN 0 AND 100"),
    ("analysis_reports", "ck_analysis_reports_context_type", "context_type IN ('seeker_pool', 'recruiter_single', 'seeker', 'demo')"),
    ("analysis_reports", "ck_analysis_reports_status", "status IN ('queued', 'running', 'available', 'succeeded', 'failed', 'deleted')"),
    ("analysis_reports", "ck_analysis_ability_score", "ability_score IS NULL OR ability_score BETWEEN 0 AND 100"),
    ("analysis_reports", "ck_analysis_evidence_coverage", "evidence_coverage IS NULL OR evidence_coverage BETWEEN 0 AND 1"),
    ("analysis_requirement_results", "ck_analysis_requirements_position", "position_no >= 1"),
    ("analysis_requirement_results", "ck_analysis_requirements_finding_type", "finding_type IN ('supported', 'partially_supported', 'needs_confirmation', 'gap')"),
    ("analysis_requirement_results", "ck_analysis_requirements_coefficient", "match_coefficient BETWEEN 0 AND 1"),
    ("apply_click_events", "ck_apply_click_platform", "platform IN ('boss', 'liepin')"),
    ("async_tasks", "ck_async_tasks_type", "task_type IN ('document_parse', 'analysis', 'rewrite', 'resume_export', 'interview_opening', 'interview_feedback', 'interview_summary', 'log_export')"),
    ("async_tasks", "ck_async_tasks_status", "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')"),
    ("async_tasks", "ck_async_tasks_counts", "usage_reserved >= 0 AND retry_count >= 0"),
    ("browser_job_drafts", "ck_browser_job_drafts_work_mode", "work_mode IS NULL OR work_mode IN ('onsite', 'hybrid', 'remote')"),
    ("browser_job_drafts", "ck_browser_job_drafts_status", "status IN ('awaiting_confirmation', 'confirmed', 'expired')"),
    ("browser_job_drafts", "ck_browser_job_drafts_platform", "platform IN ('boss', 'liepin')"),
    ("browser_sessions", "ck_browser_sessions_scope_version", "scope_version >= 1"),
    ("budget_reservations", "ck_budget_reservations_status", "status IN ('reserved', 'settled', 'released', 'unknown')"),
    ("budget_reservations", "ck_budget_reservations_actual_cost", "actual_cost_usd IS NULL OR actual_cost_usd >= 0"),
    ("budget_reservations", "ck_budget_reservations_upper_bound", "upper_bound_usd >= 0"),
    ("daily_metric_rollups", "ck_daily_metrics_revision", "revision >= 1"),
    ("daily_metric_rollups", "ck_daily_metrics_role", "registration_role IN ('seeker', 'recruiter')"),
    ("document_drafts", "ck_document_drafts_revision", "revision >= 1"),
    ("document_drafts", "ck_document_drafts_status", "status IN ('unconfirmed', 'confirmed', 'expired', 'failed')"),
    ("document_versions", "ck_document_versions_version_no", "version_no >= 1"),
    ("documents", "ck_documents_draft_revision", "draft_revision >= 1"),
    ("documents", "ck_documents_subject_type", "subject_type IN ('self_resume', 'candidate_resume', 'job_description', 'resume', 'job')"),
    ("documents", "ck_documents_type", "document_type IN ('resume', 'job_description', 'job')"),
    ("documents", "ck_documents_status", "status IN ('importing', 'parsing', 'unconfirmed', 'awaiting_confirmation', 'confirmed', 'available', 'failed', 'deleted')"),
    ("documents", "ck_documents_source_type", "source_type IN ('text', 'pdf', 'docx', 'doc')"),
    ("integrity_scan_runs", "ck_integrity_scan_runs_status", "status IN ('clean', 'findings')"),
    ("interview_feedback", "ck_interview_feedback_status", "status IN ('available', 'failed')"),
    ("interview_questions", "ck_interview_questions_type", "question_type IN ('main', 'followup')"),
    ("interview_questions", "ck_interview_questions_status", "status IN ('awaiting_answer', 'answered', 'skipped')"),
    ("interview_questions", "ck_interview_questions_position", "main_no >= 1 AND position_no >= 1"),
    ("interview_sessions", "ck_interview_sessions_revision", "revision >= 1"),
    ("interview_sessions", "ck_interview_sessions_status", "status IN ('opening', 'opening_failed', 'awaiting_answer', 'processing', 'feedback_failed', 'summary_failed', 'completed', 'ended_early', 'deleted')"),
    ("interview_summaries", "ck_interview_summaries_completion", "completion_type IN ('full', 'early', 'completed')"),
    ("job_pool_items", "ck_job_pool_platform", "platform IS NULL OR platform IN ('boss', 'liepin')"),
    ("job_pool_items", "ck_job_pool_analysis_status", "analysis_status IN ('awaiting_requirements', 'queued', 'running', 'available', 'failed', 'deleted')"),
    ("job_pool_items", "ck_job_pool_revision", "revision >= 1"),
    ("job_pool_items", "ck_job_pool_source_type", "source_type IN ('manual', 'browser_capture')"),
    ("job_preference_versions", "ck_preference_versions_version_no", "version_no >= 1"),
    ("job_preference_versions", "ck_preference_versions_source_type", "source_type IN ('self_confirmed', 'candidate_disclosed')"),
    ("job_preferences", "ck_job_preferences_status", "status IN ('active', 'archived')"),
    ("job_preferences", "ck_job_preferences_source_type", "source_type IN ('self_confirmed', 'candidate_disclosed')"),
    ("job_preferences", "ck_job_preferences_revision", "revision >= 1"),
    ("log_exports", "ck_log_exports_status", "status IN ('queued', 'exporting', 'downloadable', 'failed', 'expired')"),
    ("log_exports", "ck_log_exports_type", "log_type IN ('operations', 'security', 'tasks')"),
    ("model_call_attempts", "ck_model_call_attempts_metrics", "(input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0) AND (cost_usd IS NULL OR cost_usd >= 0) AND (duration_ms IS NULL OR duration_ms >= 0)"),
    ("model_call_attempts", "ck_model_call_attempts_status", "status IN ('running', 'succeeded', 'failed')"),
    ("product_feedback", "ck_product_feedback_rating", "rating IS NULL OR rating BETWEEN 1 AND 5"),
    ("product_feedback", "ck_product_feedback_status", "status IN ('new', 'reviewed', 'closed')"),
    ("product_feedback", "ck_product_feedback_type", "feedback_type IN ('issue', 'suggestion', 'payment_intent', 'other')"),
    ("product_feedback", "ck_product_feedback_revision", "revision >= 1"),
    ("rate_limit_buckets", "ck_rate_limit_buckets_hit_count", "hit_count >= 0"),
    ("resume_exports", "ck_resume_exports_status", "status IN ('queued', 'exporting', 'available', 'failed', 'expired')"),
    ("resume_exports", "ck_resume_exports_page_count", "page_count IS NULL OR page_count >= 1"),
    ("resume_fact_versions", "ck_fact_versions_version_no", "version_no >= 1"),
    ("resume_facts", "ck_resume_facts_revision", "revision >= 1"),
    ("resume_facts", "ck_resume_facts_status", "status IN ('active', 'archived')"),
    ("resume_rewrite_segments", "ck_rewrite_segments_decision", "current_decision IS NULL OR current_decision IN ('adopt', 'keep_original', 'revert')"),
    ("resume_rewrite_segments", "ck_rewrite_segments_decision_no", "current_decision_no >= 0"),
    ("resume_rewrite_segments", "ck_rewrite_segments_status", "status IN ('available', 'deleted')"),
    ("resume_rewrites", "ck_resume_rewrites_status", "status IN ('queued', 'running', 'available', 'failed', 'deleted')"),
    ("resume_segment_decisions", "ck_rewrite_decisions_decision_no", "decision_no >= 1"),
    ("resume_segment_decisions", "ck_rewrite_decisions_decision", "decision IN ('adopt', 'keep_original', 'revert')"),
    ("resume_variant_versions", "ck_resume_variant_versions_version_no", "version_no >= 1"),
    ("resume_variants", "ck_resume_variants_revision", "revision >= 1"),
    ("resume_variants", "ck_resume_variants_status", "status IN ('editing', 'deleted')"),
    ("security_events", "ck_security_events_outcome", "outcome IN ('succeeded', 'failed')"),
    ("site_budget_buckets", "ck_site_budget_amounts", "budget_usd >= 0 AND reserved_usd >= 0 AND settled_usd >= 0 AND unknown_usd >= 0"),
    ("site_budget_buckets", "ck_site_budget_concurrency", "concurrent_reserved >= 0 AND concurrent_limit >= 1 AND concurrent_reserved <= concurrent_limit"),
    ("stored_files", "ck_stored_files_purpose", "purpose IN ('document_source', 'resume_pdf', 'log_export')"),
    ("stored_files", "ck_stored_files_byte_size", "byte_size >= 0"),
    ("stored_files", "ck_stored_files_status", "status IN ('available', 'deleted')"),
    ("task_attempts", "ck_task_attempts_generation", "execution_generation >= 1"),
    ("task_attempts", "ck_task_attempts_status", "status IN ('queued', 'running', 'succeeded', 'failed', 'expired', 'stale', 'cancelled')"),
    ("task_input_refs", "ck_task_input_refs_version_no", "version_no IS NULL OR version_no >= 1"),
    ("task_outbox", "ck_task_outbox_status", "status IN ('pending', 'claimed', 'published')"),
    ("task_outbox", "ck_task_outbox_event_type", "event_type IN ('task.created', 'task.retry')"),
    ("task_outbox", "ck_task_outbox_attempts", "attempts >= 0"),
    ("usage_balances", "ck_usage_balances_feature", "feature IN ('analysis', 'rewrite', 'interview')"),
    ("usage_balances", "ck_usage_balances_amounts", "granted >= 0 AND consumed >= 0 AND reserved >= 0 AND consumed + reserved <= granted"),
    ("usage_grants", "ck_usage_grants_count", "count > 0"),
    ("usage_grants", "ck_usage_grants_source_type", "source_type IN ('trial', 'admin_grant', 'test')"),
    ("usage_grants", "ck_usage_grants_feature", "feature IN ('analysis', 'rewrite', 'interview')"),
    ("usage_ledger_entries", "ck_usage_ledger_feature", "feature IN ('analysis', 'rewrite', 'interview')"),
    ("usage_ledger_entries", "ck_usage_ledger_event", "event_type IN ('grant', 'reserve', 'settle', 'release')"),
    ("usage_reservations", "ck_usage_reservations_feature", "feature IN ('analysis', 'rewrite', 'interview')"),
    ("usage_reservations", "ck_usage_reservations_status", "status IN ('reserved', 'settled', 'released')"),
    ("usage_reservations", "ck_usage_reservations_count", "count > 0"),
)


def upgrade() -> None:
    """一次性验证存量数据并启用全部约束。"""

    # 早期接口曾把“采用建议”保存为过去式；现行接口和统计统一使用 adopt。
    op.execute("UPDATE resume_rewrite_segments SET current_decision = 'adopt' WHERE current_decision = 'adopted'")
    op.execute("UPDATE resume_segment_decisions SET decision = 'adopt' WHERE decision = 'adopted'")
    for table_name, constraint_name, condition in CHECKS:
        op.create_check_constraint(constraint_name, table_name, condition)


def downgrade() -> None:
    """按创建顺序的反序移除约束。"""

    for table_name, constraint_name, _ in reversed(CHECKS):
        op.drop_constraint(constraint_name, table_name, type_="check")
