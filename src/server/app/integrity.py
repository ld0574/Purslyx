"""业务库引用完整性巡检。

Purslyx 按数据库设计规范由 Service 在事务中校验跨表归属，不依赖数据库外键。
本模块提供对应的只读复核：不修改业务记录，只持久化巡检批次和脱敏发现。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from .models import (
    Account,
    AccountRole,
    AdminPermission,
    AdminRole,
    Analysis,
    AnalysisConditionResult,
    AnalysisDimensionScore,
    AnalysisEvidence,
    AnalysisRequirementResult,
    ApplyClick,
    AuditEvent,
    BrowserAuthCode,
    BrowserJobDraft,
    BrowserSession,
    BudgetReservation,
    Document,
    DocumentDraft,
    DocumentVersion,
    Export,
    Fact,
    FactVersion,
    Feedback,
    IntegrityScanFinding,
    IntegrityScanRun,
    Interview,
    InterviewAnswer,
    InterviewFeedback,
    InterviewQuestion,
    InterviewSummary,
    JobPoolItem,
    LogExport,
    ModelCall,
    OneTimeToken,
    Preference,
    PreferenceVersion,
    ResumeVariant,
    ResumeVariantVersion,
    Rewrite,
    RewriteDecision,
    RewriteEvidence,
    RewriteSegment,
    RolePermission,
    SecurityEvent,
    StoredFile,
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskInputRef,
    TaskOutbox,
    UsageBalance,
    UsageGrant,
    UsageLedger,
    UsageReservation,
    WebSession,
    WorkflowCheckpointRef,
)


@dataclass(frozen=True)
class ReferenceRule:
    """一个没有数据库外键约束的逻辑引用。"""

    name: str
    child: type[Any]
    reference_column: str
    parent: type[Any]
    child_account_column: str | None = "account_id"
    parent_account_column: str | None = "account_id"
    compare_accounts: bool = True
    parent_deleted_column: str | None = None
    child_hidden_column: str | None = None


# 所有包含 account_id 的业务表都必须指向真实账号。完整列表显式维护，新增模型时测试会提醒更新。
ACCOUNT_OWNED_MODELS: tuple[type[Any], ...] = (
    WebSession,
    OneTimeToken,
    BrowserAuthCode,
    BrowserSession,
    AccountRole,
    Document,
    DocumentVersion,
    Preference,
    Fact,
    Task,
    UsageBalance,
    UsageLedger,
    JobPoolItem,
    Analysis,
    Rewrite,
    RewriteDecision,
    ResumeVariant,
    ResumeVariantVersion,
    Export,
    Interview,
    Feedback,
    ApplyClick,
    ModelCall,
    SecurityEvent,
    LogExport,
    StoredFile,
    DocumentDraft,
    PreferenceVersion,
    FactVersion,
    RewriteSegment,
    RewriteEvidence,
    BrowserJobDraft,
    AnalysisDimensionScore,
    AnalysisRequirementResult,
    AnalysisEvidence,
    AnalysisConditionResult,
    UsageGrant,
    UsageReservation,
    BudgetReservation,
    TaskInputRef,
    TaskArtifact,
    WorkflowCheckpointRef,
    InterviewQuestion,
    InterviewAnswer,
    InterviewFeedback,
    InterviewSummary,
)


REFERENCE_RULES: tuple[ReferenceRule, ...] = (
    ReferenceRule("account_role.role", AccountRole, "role_id", AdminRole, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("role_permission.role", RolePermission, "role_id", AdminRole, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("role_permission.permission", RolePermission, "permission_id", AdminPermission, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("document_version.document", DocumentVersion, "document_id", Document, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("preference.document", Preference, "subject_document_id", Document),
    ReferenceRule("preference_version.preference", PreferenceVersion, "preference_id", Preference, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("fact.document", Fact, "document_id", Document, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("job_pool.job_document", JobPoolItem, "job_document_id", Document, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("job_pool.job_version", JobPoolItem, "job_document_version_id", DocumentVersion, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("job_pool.resume_version", JobPoolItem, "resume_version_id", DocumentVersion, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("job_pool.preference", JobPoolItem, "preference_id", Preference, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("analysis.job_pool", Analysis, "job_pool_item_id", JobPoolItem, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("analysis.resume_version", Analysis, "resume_version_id", DocumentVersion, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("analysis.job_version", Analysis, "job_version_id", DocumentVersion, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("analysis.preference", Analysis, "preference_id", Preference),
    ReferenceRule("analysis.preference_version", Analysis, "preference_version_id", PreferenceVersion),
    ReferenceRule("analysis.task", Analysis, "task_id", Task),
    ReferenceRule("rewrite.analysis", Rewrite, "analysis_id", Analysis, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("rewrite.resume_version", Rewrite, "resume_version_id", DocumentVersion, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("rewrite.task", Rewrite, "task_id", Task),
    ReferenceRule("rewrite_decision.rewrite", RewriteDecision, "rewrite_id", Rewrite),
    ReferenceRule("variant.job_pool", ResumeVariant, "job_pool_item_id", JobPoolItem, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("variant.resume_version", ResumeVariant, "source_resume_version_id", DocumentVersion, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("variant_version.variant", ResumeVariantVersion, "resume_variant_id", ResumeVariant),
    ReferenceRule("variant_version.rewrite", ResumeVariantVersion, "rewrite_id", Rewrite),
    ReferenceRule("export.variant_version", Export, "resume_variant_version_id", ResumeVariantVersion),
    ReferenceRule("export.task", Export, "task_id", Task),
    ReferenceRule("interview.job_pool", Interview, "job_pool_item_id", JobPoolItem, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("interview.analysis", Interview, "analysis_id", Analysis, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("interview.resume_version", Interview, "resume_version_id", DocumentVersion, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("interview.task", Interview, "task_id", Task),
    ReferenceRule("apply_click.job_pool", ApplyClick, "job_pool_item_id", JobPoolItem),
    ReferenceRule("model_call.task", ModelCall, "task_id", Task),
    ReferenceRule("log_export.task", LogExport, "task_id", Task),
    ReferenceRule("document_draft.document", DocumentDraft, "document_id", Document),
    ReferenceRule("fact_version.fact", FactVersion, "fact_id", Fact, parent_deleted_column="deleted_at", child_hidden_column="deleted_at"),
    ReferenceRule("fact_version.source_version", FactVersion, "source_document_version_id", DocumentVersion),
    ReferenceRule("rewrite_segment.rewrite", RewriteSegment, "rewrite_id", Rewrite),
    ReferenceRule("rewrite_evidence.segment", RewriteEvidence, "rewrite_segment_id", RewriteSegment),
    ReferenceRule("dimension.analysis", AnalysisDimensionScore, "analysis_id", Analysis),
    ReferenceRule("requirement.analysis", AnalysisRequirementResult, "analysis_id", Analysis),
    ReferenceRule("evidence.analysis", AnalysisEvidence, "analysis_id", Analysis),
    ReferenceRule("evidence.requirement", AnalysisEvidence, "requirement_result_id", AnalysisRequirementResult),
    ReferenceRule("evidence.document_version", AnalysisEvidence, "document_version_id", DocumentVersion),
    ReferenceRule("condition.analysis", AnalysisConditionResult, "analysis_id", Analysis),
    ReferenceRule("usage_ledger.task", UsageLedger, "task_id", Task),
    ReferenceRule("usage_reservation.task", UsageReservation, "task_id", Task),
    ReferenceRule("budget_reservation.task", BudgetReservation, "task_id", Task),
    ReferenceRule("budget_reservation.model_call", BudgetReservation, "model_call_id", ModelCall),
    ReferenceRule("task_input.task", TaskInputRef, "task_id", Task),
    ReferenceRule("task_outbox.task", TaskOutbox, "task_id", Task, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("task_attempt.task", TaskAttempt, "task_id", Task, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("task_artifact.task", TaskArtifact, "task_id", Task),
    ReferenceRule("task_artifact.file", TaskArtifact, "file_id", StoredFile),
    ReferenceRule("checkpoint.task", WorkflowCheckpointRef, "task_id", Task, parent_deleted_column="deleted_at", child_hidden_column="content_access_revoked_at"),
    ReferenceRule("question.interview", InterviewQuestion, "interview_id", Interview),
    ReferenceRule("question.parent", InterviewQuestion, "parent_question_id", InterviewQuestion),
    ReferenceRule("answer.interview", InterviewAnswer, "interview_id", Interview),
    ReferenceRule("answer.question", InterviewAnswer, "question_id", InterviewQuestion),
    ReferenceRule("interview_feedback.interview", InterviewFeedback, "interview_id", Interview),
    ReferenceRule("interview_feedback.question", InterviewFeedback, "question_id", InterviewQuestion),
    ReferenceRule("interview_summary.interview", InterviewSummary, "interview_id", Interview),
    ReferenceRule("usage_grant.operator", UsageGrant, "operator_account_id", Account, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("feedback.reviewer", Feedback, "reviewed_by_account_id", Account, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("audit.operator", AuditEvent, "operator_account_id", Account, child_account_column=None, parent_account_column=None, compare_accounts=False),
    ReferenceRule("audit.target", AuditEvent, "target_account_id", Account, child_account_column=None, parent_account_column=None, compare_accounts=False),
)


def _resource_id(model: type[Any], row_id: int, public_value: str | None) -> str:
    """优先使用公开 ID；没有公开 ID 的明细表只记录表内数字 ID。"""

    return str(public_value or row_id)


def _finding(
    finding_type: str,
    resource_type: str,
    resource_public_id: str,
    rule: str,
    reference_value: int | None,
) -> dict[str, Any]:
    return {
        "finding_type": finding_type,
        "resource_type": resource_type,
        "resource_public_id": resource_public_id,
        "details": {"rule": rule, "reference_value": reference_value},
    }


def collect_integrity_findings(db: Session) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """执行所有引用检查并返回脱敏发现和扫描计数。"""

    findings: list[dict[str, Any]] = []
    checked_rows = 0

    for model in ACCOUNT_OWNED_MODELS:
        account = aliased(Account)
        public_column = getattr(model, "public_id", None)
        columns = [model.id, model.account_id, account.id]
        if public_column is not None:
            columns.append(public_column)
        rows = db.execute(
            select(*columns)
            .select_from(model)
            .outerjoin(account, account.id == model.account_id)
            .where(model.account_id.is_not(None))
        ).all()
        checked_rows += len(rows)
        for row in rows:
            public_value = row[3] if public_column is not None else None
            if row[2] is None:
                findings.append(
                    _finding(
                        "missing_account",
                        model.__tablename__,
                        _resource_id(model, row[0], public_value),
                        f"{model.__tablename__}.account_id",
                        row[1],
                    )
                )

    for rule in REFERENCE_RULES:
        parent = aliased(rule.parent)
        child_public = getattr(rule.child, "public_id", None)
        child_account = getattr(rule.child, rule.child_account_column) if rule.child_account_column else None
        parent_account = getattr(parent, rule.parent_account_column) if rule.parent_account_column else None
        parent_deleted = getattr(parent, rule.parent_deleted_column) if rule.parent_deleted_column else None
        child_hidden = getattr(rule.child, rule.child_hidden_column) if rule.child_hidden_column else None
        reference = getattr(rule.child, rule.reference_column)
        columns = [rule.child.id, reference, parent.id]
        for optional_column in (child_public, child_account, parent_account, parent_deleted, child_hidden):
            if optional_column is not None:
                columns.append(optional_column)
        rows = db.execute(
            select(*columns)
            .select_from(rule.child)
            .outerjoin(parent, parent.id == reference)
            .where(reference.is_not(None))
        ).all()
        checked_rows += len(rows)
        for row in rows:
            index = 3
            public_value = row[index] if child_public is not None else None
            index += int(child_public is not None)
            child_account_value = row[index] if child_account is not None else None
            index += int(child_account is not None)
            parent_account_value = row[index] if parent_account is not None else None
            index += int(parent_account is not None)
            parent_deleted_value = row[index] if parent_deleted is not None else None
            index += int(parent_deleted is not None)
            child_hidden_value = row[index] if child_hidden is not None else None
            resource_id = _resource_id(rule.child, row[0], public_value)
            if row[2] is None:
                findings.append(
                    _finding("orphan_reference", rule.child.__tablename__, resource_id, rule.name, row[1])
                )
                continue
            if (
                rule.compare_accounts
                and child_account is not None
                and parent_account is not None
                and child_account_value != parent_account_value
            ):
                findings.append(
                    _finding("cross_account_reference", rule.child.__tablename__, resource_id, rule.name, row[1])
                )
            if parent_deleted is not None and parent_deleted_value is not None and child_hidden_value is None:
                findings.append(
                    _finding("deleted_parent_visible_child", rule.child.__tablename__, resource_id, rule.name, row[1])
                )

    return findings, {"checked_rows": checked_rows, "checked_rules": len(ACCOUNT_OWNED_MODELS) + len(REFERENCE_RULES)}


def run_integrity_scan(db: Session, *, max_persisted_findings: int = 1000) -> IntegrityScanRun:
    """执行巡检并持久化批次；发现只含标识和规则，不包含业务正文。"""

    if max_persisted_findings < 1:
        raise ValueError("max_persisted_findings 必须大于 0")
    findings, counters = collect_integrity_findings(db)
    by_type = Counter(item["finding_type"] for item in findings)
    run = IntegrityScanRun(
        status="clean" if not findings else "findings",
        summary={
            **counters,
            "finding_count": len(findings),
            "persisted_finding_count": min(len(findings), max_persisted_findings),
            "truncated": len(findings) > max_persisted_findings,
            "by_type": dict(sorted(by_type.items())),
        },
    )
    db.add(run)
    db.flush()
    for item in findings[:max_persisted_findings]:
        db.add(IntegrityScanFinding(scan_run_id=run.id, **item))
    db.flush()
    return run
