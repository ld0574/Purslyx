"""数据库级幂等与面试轮次约束回归。"""

from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

from server.app.main import app
from server.app.models import (
    BrowserJobDraft,
    Document,
    DocumentVersion,
    Fact,
    FactVersion,
    Feedback,
    InterviewAnswer,
    InterviewFeedback,
    InterviewSummary,
    LogExport,
    Preference,
    PreferenceVersion,
    ResumeVariant,
    ResumeVariantVersion,
    RewriteDecision,
    Task,
    TaskAttempt,
    UsageGrant,
)


def _unique_names(model: type) -> set[str]:
    table = model.__table__
    return {
        item.name
        for item in [*table.indexes, *table.constraints]
        if item.name and (isinstance(item, UniqueConstraint) or bool(getattr(item, "unique", False)))
    }


def test_business_idempotency_is_enforced_by_postgres_metadata() -> None:
    """应用层重放判断之外，数据库仍须拒绝并发产生第二份事实。"""

    expected = {
        Document: "uk_documents_account_idempotency",
        DocumentVersion: "uk_document_versions_account_idempotency",
        Preference: "uk_preferences_account_idempotency",
        PreferenceVersion: "uk_preference_versions_account_idempotency",
        Fact: "uk_facts_account_idempotency",
        FactVersion: "uk_fact_versions_account_idempotency",
        Task: "uk_tasks_account_type_idempotency",
        RewriteDecision: "uk_rewrite_decisions_account_idempotency",
        ResumeVariant: "uk_resume_variants_account_idempotency",
        ResumeVariantVersion: "uk_variant_versions_account_idempotency",
        Feedback: "uk_feedback_account_idempotency",
        BrowserJobDraft: "uk_browser_drafts_account_idempotency",
        LogExport: "uk_log_exports_account_idempotency",
        UsageGrant: "uk_usage_grant_trial_batch",
        InterviewAnswer: "uk_interview_answers_account_idempotency",
    }
    for model, name in expected.items():
        assert name in _unique_names(model), f"{model.__name__} 缺少 {name}"


def test_interview_and_worker_generations_have_single_fact_constraints() -> None:
    assert "uk_task_attempt_generation" in _unique_names(TaskAttempt)
    assert "uk_interview_feedback_question" in _unique_names(InterviewFeedback)
    assert "uk_interview_summary" in _unique_names(InterviewSummary)


def test_integrity_conflicts_are_redacted_as_business_conflicts() -> None:
    assert IntegrityError in app.exception_handlers
