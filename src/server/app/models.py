"""Purslyx 业务模型。

模型刻意保留公共业务事实和版本字段；跨表归属检查由 Service 完成，避免把权限判断
隐藏在 ORM 级联行为中。所有对外标识使用 public_id，内部自增 ID 不出现在 API。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    """返回带时区的 UTC 当前时间。"""

    return datetime.now(timezone.utc)


def public_id() -> str:
    """生成 API 使用的随机 UUID。"""

    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("email_normalized", name="uk_accounts_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    registration_role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_verification", nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OneTimeToken(Base):
    __tablename__ = "account_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    token_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BrowserAuthCode(Base):
    __tablename__ = "browser_auth_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    origin: Mapped[str] = mapped_column(String(512), nullable=False)
    nonce: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    scope_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AdminPermission(Base):
    __tablename__ = "admin_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class AdminRole(TimestampMixin, Base):
    __tablename__ = "admin_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class RolePermission(Base):
    __tablename__ = "admin_role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uk_role_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    permission_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)


class AccountRole(Base):
    __tablename__ = "account_admin_roles"
    __table_args__ = (UniqueConstraint("account_id", "role_id", name="uk_account_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_account_status", "account_id", "status", "updated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), default="未命名资料", nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="importing", nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    draft_content: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    draft_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uk_document_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    document_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), default="document-content-v1", nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Preference(TimestampMixin, Base):
    __tablename__ = "job_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    subject_document_id: Mapped[int | None] = mapped_column(Integer, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="self_confirmed", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Fact(TimestampMixin, Base):
    __tablename__ = "resume_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    document_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    fact_category: Mapped[str] = mapped_column(String(40), nullable=False)
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_segment_key: Mapped[str | None] = mapped_column(String(96))
    source_type: Mapped[str] = mapped_column(String(32), default="user_added", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Task(TimestampMixin, Base):
    __tablename__ = "async_tasks"
    __table_args__ = (Index("ix_tasks_account_status", "account_id", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True, default="queued", nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(80))
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    required_actions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    usage_feature: Mapped[str | None] = mapped_column(String(32))
    usage_reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class UsageBalance(Base):
    __tablename__ = "usage_balances"
    __table_args__ = (UniqueConstraint("account_id", "feature", name="uk_usage_balance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class UsageLedger(Base):
    __tablename__ = "usage_ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    feature: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class JobPoolItem(TimestampMixin, Base):
    __tablename__ = "job_pool_items"
    __table_args__ = (Index("ix_job_pool_account_status", "account_id", "analysis_status", "updated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    platform: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    source_url_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    job_title: Mapped[str] = mapped_column(String(200), default="未命名岗位", nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200))
    job_fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    job_document_id: Mapped[int | None] = mapped_column(Integer, index=True)
    job_document_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
    resume_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
    preference_id: Mapped[int | None] = mapped_column(Integer, index=True)
    analysis_status: Mapped[str] = mapped_column(String(32), default="awaiting_requirements", nullable=False)
    blocking_reasons: Mapped[list[str] | None] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Analysis(TimestampMixin, Base):
    __tablename__ = "analysis_reports"
    __table_args__ = (Index("ix_analysis_account_status", "account_id", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    context_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True, nullable=False)
    job_pool_item_id: Mapped[int | None] = mapped_column(Integer, index=True)
    resume_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    job_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    preference_id: Mapped[int | None] = mapped_column(Integer, index=True)
    job_category: Mapped[str] = mapped_column(String(32), default="general", nullable=False)
    ability_score: Mapped[float | None] = mapped_column(Float)
    evidence_coverage: Mapped[float | None] = mapped_column(Float)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    scoring_rule_version: Mapped[str] = mapped_column(String(80), default="ability-v0.1", nullable=False)
    result_schema_version: Mapped[str] = mapped_column(String(80), default="analysis-result-v1", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), default="analysis-local-v1", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Rewrite(TimestampMixin, Base):
    __tablename__ = "resume_rewrites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    resume_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RewriteDecision(Base):
    __tablename__ = "resume_segment_decisions"
    __table_args__ = (Index("ix_rewrite_decision_rewrite_segment", "rewrite_id", "segment_key", "decision_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    rewrite_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    segment_key: Mapped[str] = mapped_column(String(96), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    edited_text: Mapped[str | None] = mapped_column(Text)
    decision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ResumeVariant(TimestampMixin, Base):
    __tablename__ = "resume_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    job_pool_item_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    source_resume_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="editing", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ResumeVariantVersion(Base):
    __tablename__ = "resume_variant_versions"
    __table_args__ = (UniqueConstraint("resume_variant_id", "version_no", name="uk_resume_variant_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    resume_variant_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    layout: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), default="resume-template-v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Export(Base):
    __tablename__ = "resume_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    resume_variant_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True, nullable=False)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interview(TimestampMixin, Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    job_pool_item_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    resume_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="opening", index=True, nullable=False)
    questions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    answers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    current_question_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    usage_settled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Feedback(TimestampMixin, Base):
    __tablename__ = "product_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    payment_intent: Mapped[str | None] = mapped_column(String(32))
    context_type: Mapped[str | None] = mapped_column(String(32))
    context_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ApplyClick(Base):
    __tablename__ = "apply_click_events"
    __table_args__ = (UniqueConstraint("account_id", "click_token_hash", name="uk_apply_click_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    job_pool_item_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    target_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    click_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ModelCall(Base):
    __tablename__ = "model_call_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    feature: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(80))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    operator_account_id: Mapped[int | None] = mapped_column(Integer, index=True)
    target_account_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    before_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int | None] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    client_type: Mapped[str | None] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LogExport(Base):
    __tablename__ = "log_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    log_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MetricRollup(Base):
    __tablename__ = "daily_metric_rollups"
    __table_args__ = (UniqueConstraint("metric_date", "registration_role", "feature", name="uk_daily_metric"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    registration_role: Mapped[str] = mapped_column(String(32), nullable=False)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(80), default="daily-metrics-v1", nullable=False)
    source_watermark_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
