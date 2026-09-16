"""Purslyx 业务模型。

模型刻意保留公共业务事实和版本字段；跨表归属检查由 Service 完成，避免把权限判断
隐藏在 ORM 级联行为中。所有对外标识使用 public_id，内部自增 ID 不出现在 API。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uk_accounts_email"),
        CheckConstraint("registration_role IN ('seeker', 'recruiter')", name="ck_accounts_registration_role"),
        CheckConstraint(
            "status IN ('pending_verification', 'active', 'suspended')",
            name="ck_accounts_status",
        ),
        CheckConstraint("revision >= 1", name="ck_accounts_revision"),
    )

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
    __table_args__ = (
        CheckConstraint(
            "token_type IN ('verify_email', 'reset_password', 'recover_account')",
            name="ck_account_tokens_type",
        ),
    )

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
    __table_args__ = (CheckConstraint("scope_version >= 1", name="ck_browser_sessions_scope_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    scope_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RateLimitBucket(Base):
    """基于 PostgreSQL 的限频桶；避免多进程部署时只在内存中限频。"""

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (CheckConstraint("hit_count >= 0", name="ck_rate_limit_buckets_hit_count"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AdminPermission(Base):
    __tablename__ = "admin_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class AdminRole(TimestampMixin, Base):
    __tablename__ = "admin_roles"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_admin_roles_status"),
        CheckConstraint("revision >= 1", name="ck_admin_roles_revision"),
    )

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
    __table_args__ = (
        Index("ix_documents_account_status", "account_id", "status", "updated_at"),
        Index(
            "uk_documents_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("document_type IN ('resume', 'job_description', 'job')", name="ck_documents_type"),
        CheckConstraint(
            "subject_type IN ('self_resume', 'candidate_resume', 'job_description', 'resume', 'job')",
            name="ck_documents_subject_type",
        ),
        CheckConstraint("source_type IN ('text', 'pdf', 'docx', 'doc')", name="ck_documents_source_type"),
        CheckConstraint(
            "status IN ('importing', 'parsing', 'unconfirmed', 'awaiting_confirmation', "
            "'confirmed', 'available', 'failed', 'deleted')",
            name="ck_documents_status",
        ),
        CheckConstraint("draft_revision >= 1", name="ck_documents_draft_revision"),
    )

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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    draft_content: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    draft_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uk_document_version"),
        Index(
            "uk_document_versions_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("version_no >= 1", name="ck_document_versions_version_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    document_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(80), default="document-content-v1", nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Preference(TimestampMixin, Base):
    __tablename__ = "job_preferences"
    __table_args__ = (
        Index(
            "uk_preferences_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint(
            "source_type IN ('self_confirmed', 'candidate_disclosed')",
            name="ck_job_preferences_source_type",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="ck_job_preferences_status"),
        CheckConstraint("revision >= 1", name="ck_job_preferences_revision"),
    )

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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Fact(TimestampMixin, Base):
    __tablename__ = "resume_facts"
    __table_args__ = (
        Index(
            "uk_facts_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("status IN ('active', 'archived')", name="ck_resume_facts_status"),
        CheckConstraint("revision >= 1", name="ck_resume_facts_revision"),
    )

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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Task(TimestampMixin, Base):
    __tablename__ = "async_tasks"
    __table_args__ = (
        Index("ix_tasks_account_status", "account_id", "status", "created_at"),
        Index(
            "uk_tasks_account_type_idempotency",
            "account_id",
            "task_type",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint(
            "task_type IN ('document_parse', 'analysis', 'rewrite', 'resume_export', "
            "'interview_opening', 'interview_feedback', 'interview_summary', 'log_export')",
            name="ck_async_tasks_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_async_tasks_status",
        ),
        CheckConstraint("usage_reserved >= 0 AND retry_count >= 0", name="ck_async_tasks_counts"),
    )

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
    request_hash: Mapped[str | None] = mapped_column(String(64))
    usage_feature: Mapped[str | None] = mapped_column(String(32))
    usage_reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class UsageBalance(Base):
    __tablename__ = "usage_balances"
    __table_args__ = (
        UniqueConstraint("account_id", "feature", name="uk_usage_balance"),
        CheckConstraint("feature IN ('analysis', 'rewrite', 'interview')", name="ck_usage_balances_feature"),
        CheckConstraint(
            "granted >= 0 AND consumed >= 0 AND reserved >= 0 AND consumed + reserved <= granted",
            name="ck_usage_balances_amounts",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class UsageLedger(Base):
    __tablename__ = "usage_ledger_entries"
    __table_args__ = (
        CheckConstraint("feature IN ('analysis', 'rewrite', 'interview')", name="ck_usage_ledger_feature"),
        CheckConstraint("event_type IN ('grant', 'reserve', 'settle', 'release')", name="ck_usage_ledger_event"),
    )

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
    __table_args__ = (
        Index("ix_job_pool_account_status", "account_id", "analysis_status", "updated_at"),
        Index(
            "uk_job_pool_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("source_type IN ('manual', 'browser_capture')", name="ck_job_pool_source_type"),
        CheckConstraint("platform IS NULL OR platform IN ('boss', 'liepin')", name="ck_job_pool_platform"),
        CheckConstraint(
            "analysis_status IN ('awaiting_requirements', 'queued', 'running', 'available', 'failed', 'deleted')",
            name="ck_job_pool_analysis_status",
        ),
        CheckConstraint("revision >= 1", name="ck_job_pool_revision"),
    )

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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Analysis(TimestampMixin, Base):
    __tablename__ = "analysis_reports"
    __table_args__ = (
        Index("ix_analysis_account_status", "account_id", "status", "created_at"),
        CheckConstraint(
            "context_type IN ('seeker_pool', 'recruiter_single', 'seeker', 'demo')",
            name="ck_analysis_reports_context_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'available', 'succeeded', 'failed', 'deleted')",
            name="ck_analysis_reports_status",
        ),
        CheckConstraint("ability_score IS NULL OR ability_score BETWEEN 0 AND 100", name="ck_analysis_ability_score"),
        CheckConstraint(
            "evidence_coverage IS NULL OR evidence_coverage BETWEEN 0 AND 1",
            name="ck_analysis_evidence_coverage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    context_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True, nullable=False)
    job_pool_item_id: Mapped[int | None] = mapped_column(Integer, index=True)
    resume_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    job_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    preference_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # 分析必须冻结具体的岗位期望版本，不能只指向会继续变化的父记录。
    preference_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
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
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'available', 'failed', 'deleted')",
            name="ck_resume_rewrites_status",
        ),
    )

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
    __table_args__ = (
        Index("ix_rewrite_decision_rewrite_segment", "rewrite_id", "segment_key", "decision_no"),
        Index(
            "uk_rewrite_decisions_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("decision IN ('adopt', 'keep_original', 'revert')", name="ck_rewrite_decisions_decision"),
        CheckConstraint("decision_no >= 1", name="ck_rewrite_decisions_decision_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    rewrite_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    segment_key: Mapped[str] = mapped_column(String(96), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    edited_text: Mapped[str | None] = mapped_column(Text)
    decision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ResumeVariant(TimestampMixin, Base):
    __tablename__ = "resume_variants"
    __table_args__ = (
        Index(
            "uk_resume_variants_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("status IN ('editing', 'deleted')", name="ck_resume_variants_status"),
        CheckConstraint("revision >= 1", name="ck_resume_variants_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    job_pool_item_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    source_resume_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="editing", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ResumeVariantVersion(Base):
    __tablename__ = "resume_variant_versions"
    __table_args__ = (
        UniqueConstraint("resume_variant_id", "version_no", name="uk_resume_variant_version"),
        Index(
            "uk_variant_versions_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("version_no >= 1", name="ck_resume_variant_versions_version_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    resume_variant_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    layout: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rewrite_id: Mapped[int | None] = mapped_column(Integer, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(80), default="resume-template-v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Export(Base):
    __tablename__ = "resume_exports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'exporting', 'available', 'failed', 'expired')",
            name="ck_resume_exports_status",
        ),
        CheckConstraint("page_count IS NULL OR page_count >= 1", name="ck_resume_exports_page_count"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    resume_variant_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True, nullable=False)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interview(TimestampMixin, Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('opening', 'opening_failed', 'awaiting_answer', 'processing', "
            "'feedback_failed', 'summary_failed', 'completed', 'ended_early', 'deleted')",
            name="ck_interview_sessions_status",
        ),
        CheckConstraint("revision >= 1", name="ck_interview_sessions_revision"),
    )

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
    __table_args__ = (
        Index(
            "uk_feedback_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint(
            "feedback_type IN ('issue', 'suggestion', 'payment_intent', 'other')",
            name="ck_product_feedback_type",
        ),
        CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="ck_product_feedback_rating"),
        CheckConstraint("status IN ('new', 'reviewed', 'closed')", name="ck_product_feedback_status"),
        CheckConstraint("revision >= 1", name="ck_product_feedback_revision"),
    )

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
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reviewed_by_account_id: Mapped[int | None] = mapped_column(Integer, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ApplyClick(Base):
    __tablename__ = "apply_click_events"
    __table_args__ = (
        UniqueConstraint("account_id", "click_token_hash", name="uk_apply_click_token"),
        CheckConstraint("platform IN ('boss', 'liepin')", name="ck_apply_click_platform"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    job_pool_item_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    # 统计事实只保存链接摘要，原始链接仍只留在岗位记录中。
    source_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    click_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ModelCall(Base):
    __tablename__ = "model_call_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_model_call_attempts_status"),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0) AND "
            "(cost_usd IS NULL OR cost_usd >= 0) AND "
            "(duration_ms IS NULL OR duration_ms >= 0)",
            name="ck_model_call_attempts_metrics",
        ),
    )

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
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    error_code: Mapped[str | None] = mapped_column(String(80))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        Index(
            "uk_admin_audit_action_idempotency",
            "operator_account_id",
            "action",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SecurityEvent(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('succeeded', 'failed')", name="ck_security_events_outcome"),
    )

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
    __table_args__ = (
        Index(
            "uk_log_exports_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("log_type IN ('operations', 'security', 'tasks')", name="ck_log_exports_type"),
        CheckConstraint(
            "status IN ('queued', 'exporting', 'downloadable', 'failed', 'expired')",
            name="ck_log_exports_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    log_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MetricRollup(Base):
    __tablename__ = "daily_metric_rollups"
    __table_args__ = (
        UniqueConstraint("metric_date", "registration_role", "feature", name="uk_daily_metric"),
        CheckConstraint("registration_role IN ('seeker', 'recruiter')", name="ck_daily_metrics_role"),
        CheckConstraint("revision >= 1", name="ck_daily_metrics_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    registration_role: Mapped[str] = mapped_column(String(32), nullable=False)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(80), default="daily-metrics-v1", nullable=False)
    source_watermark_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


# 以下模型对应评审后的数据库设计中尚未在第一版草案出现的明细表。跨模块 ID 仍由
# Service 在事务内校验，不创建数据库外键，保持项目数据库规范的可清理与可巡检特性。


class StoredFile(Base):
    """私有原件、成品和导出文件的元数据。"""

    __tablename__ = "stored_files"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('document_source', 'resume_pdf', 'log_export')",
            name="ck_stored_files_purpose",
        ),
        CheckConstraint("byte_size >= 0", name="ck_stored_files_byte_size"),
        CheckConstraint("status IN ('available', 'deleted')", name="ck_stored_files_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(160), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="available", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class DocumentDraft(Base):
    """资料解析草稿；确认后保留最小状态，正文可按清理策略移除。"""

    __tablename__ = "document_drafts"
    __table_args__ = (
        Index("ix_document_drafts_account_document", "account_id", "document_id", "created_at"),
        CheckConstraint("revision >= 1", name="ck_document_drafts_revision"),
        CheckConstraint(
            "status IN ('unconfirmed', 'confirmed', 'expired', 'failed')",
            name="ck_document_drafts_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    document_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="unconfirmed", nullable=False)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    missing_field_codes: Mapped[list[str] | None] = mapped_column(JSON)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class PreferenceVersion(Base):
    """完整的岗位期望不可变版本。"""

    __tablename__ = "job_preference_versions"
    __table_args__ = (
        UniqueConstraint("preference_id", "version_no", name="uk_preference_version"),
        Index(
            "uk_preference_versions_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("version_no >= 1", name="ck_preference_versions_version_no"),
        CheckConstraint(
            "source_type IN ('self_confirmed', 'candidate_disclosed')",
            name="ck_preference_versions_source_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    preference_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="self_confirmed", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class FactVersion(Base):
    """补充事实不可变版本。"""

    __tablename__ = "resume_fact_versions"
    __table_args__ = (
        UniqueConstraint("fact_id", "version_no", name="uk_fact_version"),
        Index(
            "uk_fact_versions_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("version_no >= 1", name="ck_fact_versions_version_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    fact_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_category: Mapped[str] = mapped_column(String(40), nullable=False)
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_document_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_segment_key: Mapped[str | None] = mapped_column(String(96))
    source_type: Mapped[str] = mapped_column(String(32), default="user_added", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RewriteSegment(Base):
    """逐段改写结果，避免把所有段落塞进不可筛选的大 JSON。"""

    __tablename__ = "resume_rewrite_segments"
    __table_args__ = (
        CheckConstraint(
            "current_decision IS NULL OR current_decision IN ('adopt', 'keep_original', 'revert')",
            name="ck_rewrite_segments_decision",
        ),
        CheckConstraint("current_decision_no >= 0", name="ck_rewrite_segments_decision_no"),
        CheckConstraint("status IN ('available', 'deleted')", name="ck_rewrite_segments_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    rewrite_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    segment_key: Mapped[str] = mapped_column(String(96), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_text: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    current_decision: Mapped[str | None] = mapped_column(String(24))
    current_decision_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="available", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RewriteEvidence(Base):
    """改写建议引用的简历段落或已确认事实。"""

    __tablename__ = "resume_rewrite_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    rewrite_segment_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(96), nullable=False)
    quote: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BrowserJobDraft(Base):
    """BOSS／猎聘浏览器侧上传的短期岗位草稿。"""

    __tablename__ = "browser_job_drafts"
    __table_args__ = (
        UniqueConstraint("account_id", "platform", "source_url_hash", "content_hash", name="uk_browser_draft_dedupe"),
        Index(
            "uk_browser_drafts_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("platform IN ('boss', 'liepin')", name="ck_browser_job_drafts_platform"),
        CheckConstraint(
            "work_mode IS NULL OR work_mode IN ('onsite', 'hybrid', 'remote')",
            name="ck_browser_job_drafts_work_mode",
        ),
        CheckConstraint(
            "status IN ('awaiting_confirmation', 'confirmed', 'expired')",
            name="ck_browser_job_drafts_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_url_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    capture_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(200))
    company_name: Mapped[str | None] = mapped_column(String(200))
    location_text: Mapped[str | None] = mapped_column(String(300))
    work_mode: Mapped[str | None] = mapped_column(String(24))
    salary_text: Mapped[str | None] = mapped_column(String(300))
    job_description_text: Mapped[str | None] = mapped_column(Text)
    missing_field_codes: Mapped[list[str] | None] = mapped_column(JSON)
    captured_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="awaiting_confirmation", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AnalysisDimensionScore(Base):
    """报告固定能力维度得分。"""

    __tablename__ = "analysis_dimension_scores"
    __table_args__ = (
        UniqueConstraint("analysis_id", "dimension_key", name="uk_analysis_dimension"),
        CheckConstraint("base_weight BETWEEN 0 AND 1", name="ck_analysis_dimensions_base_weight"),
        CheckConstraint("effective_weight BETWEEN 0 AND 1", name="ck_analysis_dimensions_effective_weight"),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="ck_analysis_dimensions_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    dimension_key: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    base_weight: Mapped[float] = mapped_column(Float, nullable=False)
    effective_weight: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)


class AnalysisRequirementResult(Base):
    """报告中逐条 JD 要求的结论。"""

    __tablename__ = "analysis_requirement_results"
    __table_args__ = (
        UniqueConstraint("analysis_id", "requirement_id", name="uk_analysis_requirement"),
        CheckConstraint("position_no >= 1", name="ck_analysis_requirements_position"),
        CheckConstraint(
            "finding_type IN ('supported', 'partially_supported', 'needs_confirmation', 'gap')",
            name="ck_analysis_requirements_finding_type",
        ),
        CheckConstraint("match_coefficient BETWEEN 0 AND 1", name="ck_analysis_requirements_coefficient"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    requirement_id: Mapped[str] = mapped_column(String(96), nullable=False)
    dimension_key: Mapped[str] = mapped_column(String(48), nullable=False)
    position_no: Mapped[int] = mapped_column(Integer, nullable=False)
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    finding_type: Mapped[str] = mapped_column(String(32), nullable=False)
    match_coefficient: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class AnalysisEvidence(Base):
    """逐条要求的原文证据引用。"""

    __tablename__ = "analysis_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    requirement_result_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    document_version_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    segment_key: Mapped[str | None] = mapped_column(String(96))
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AnalysisConditionResult(Base):
    """岗位方向、地点、办公方式、薪资的条件对照。"""

    __tablename__ = "analysis_condition_results"
    __table_args__ = (
        UniqueConstraint("analysis_id", "condition_code", name="uk_analysis_condition"),
        CheckConstraint(
            "condition_code IN ('job_title', 'location', 'work_mode', 'salary')",
            name="ck_analysis_conditions_code",
        ),
        CheckConstraint(
            "result_status IN ('matched', 'conflicted', 'unknown', 'unrestricted')",
            name="ck_analysis_conditions_status",
        ),
        CheckConstraint(
            "strength IS NULL OR strength IN ('prefer', 'important', 'required', 'negotiable')",
            name="ck_analysis_conditions_strength",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    condition_code: Mapped[str] = mapped_column(String(32), nullable=False)
    result_status: Mapped[str] = mapped_column(String(24), nullable=False)
    preference_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    job_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    strength: Mapped[str | None] = mapped_column(String(24))
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class UsageGrant(Base):
    """试用或后台追加的不可变发放事实。"""

    __tablename__ = "usage_grants"
    __table_args__ = (
        Index(
            "uk_usage_grant_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uk_usage_grant_trial_batch",
            "account_id",
            "feature",
            "batch_key",
            unique=True,
            postgresql_where=text("batch_key IS NOT NULL"),
        ),
        CheckConstraint("feature IN ('analysis', 'rewrite', 'interview')", name="ck_usage_grants_feature"),
        CheckConstraint("count > 0", name="ck_usage_grants_count"),
        CheckConstraint("source_type IN ('trial', 'admin_grant', 'test')", name="ck_usage_grants_source_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    operator_account_id: Mapped[int | None] = mapped_column(Integer, index=True)
    feature: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    batch_key: Mapped[str | None] = mapped_column(String(80), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    before_available: Mapped[int] = mapped_column(Integer, nullable=False)
    after_available: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UsageReservation(Base):
    """计次任务的预留到结算／释放生命周期。"""

    __tablename__ = "usage_reservations"
    __table_args__ = (
        UniqueConstraint("task_id", "feature", name="uk_usage_reservation_task_feature"),
        CheckConstraint("feature IN ('analysis', 'rewrite', 'interview')", name="ck_usage_reservations_feature"),
        CheckConstraint("count > 0", name="ck_usage_reservations_count"),
        CheckConstraint("status IN ('reserved', 'settled', 'released')", name="ck_usage_reservations_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="reserved", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelPriceVersion(Base):
    """模型价格版本；未知价格不能被当成零成本。"""

    __tablename__ = "model_price_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_usd_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    cached_input_usd_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    output_usd_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    verified_on: Mapped[str | None] = mapped_column(String(10))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SiteBudgetBucket(Base):
    """按上海自然日控制的全站预算和并发槽。"""

    __tablename__ = "site_budget_buckets"
    __table_args__ = (
        UniqueConstraint("metric_date", name="uk_budget_date"),
        CheckConstraint(
            "budget_usd >= 0 AND reserved_usd >= 0 AND settled_usd >= 0 AND unknown_usd >= 0",
            name="ck_site_budget_amounts",
        ),
        CheckConstraint(
            "concurrent_reserved >= 0 AND concurrent_limit >= 1 AND concurrent_reserved <= concurrent_limit",
            name="ck_site_budget_concurrency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_date: Mapped[str] = mapped_column(String(10), nullable=False)
    budget_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    settled_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    unknown_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    concurrent_reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    concurrent_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class BudgetReservation(Base):
    """单次模型调用的预算预留。"""

    __tablename__ = "budget_reservations"
    __table_args__ = (
        CheckConstraint("upper_bound_usd >= 0", name="ck_budget_reservations_upper_bound"),
        CheckConstraint("actual_cost_usd IS NULL OR actual_cost_usd >= 0", name="ck_budget_reservations_actual_cost"),
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released', 'unknown')",
            name="ck_budget_reservations_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    model_call_id: Mapped[int | None] = mapped_column(Integer, index=True)
    metric_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    call_key: Mapped[str | None] = mapped_column(String(160), unique=True, index=True)
    upper_bound_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    status: Mapped[str] = mapped_column(String(24), default="reserved", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TaskInputRef(Base):
    """任务创建时冻结的资源与版本引用。"""

    __tablename__ = "task_input_refs"
    __table_args__ = (
        CheckConstraint("version_no IS NULL OR version_no >= 1", name="ck_task_input_refs_version_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_public_id: Mapped[str] = mapped_column(String(96), nullable=False)
    version_no: Mapped[int | None] = mapped_column(Integer)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TaskOutbox(Base):
    """事务提交后再发布到队列的持久消息。"""

    __tablename__ = "task_outbox"
    __table_args__ = (
        CheckConstraint("event_type IN ('task.created', 'task.retry')", name="ck_task_outbox_event_type"),
        CheckConstraint("status IN ('pending', 'claimed', 'published')", name="ck_task_outbox_status"),
        CheckConstraint("attempts >= 0", name="ck_task_outbox_attempts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(120), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TaskAttempt(Base):
    """任务执行代次、租约和结果摘要。"""

    __tablename__ = "task_attempts"
    __table_args__ = (
        Index("uk_task_attempt_generation", "task_id", "execution_generation", unique=True),
        CheckConstraint("execution_generation >= 1", name="ck_task_attempts_generation"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'expired', 'stale', 'cancelled')",
            name="ck_task_attempts_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    execution_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskArtifact(Base):
    """可恢复步骤的结构化产物引用，不直接向用户暴露。"""

    __tablename__ = "task_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    file_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WorkflowCheckpointRef(Base):
    """业务任务到工作流检查点的最小映射。"""

    __tablename__ = "workflow_checkpoint_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(48), nullable=False)
    thread_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    checkpoint_ref: Mapped[str | None] = mapped_column(String(160))
    content_access_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InterviewQuestion(Base):
    """面试题目明细，问题正文仍受账号删除状态控制。"""

    __tablename__ = "interview_questions"
    __table_args__ = (
        CheckConstraint("question_type IN ('main', 'followup')", name="ck_interview_questions_type"),
        CheckConstraint("main_no >= 1 AND position_no >= 1", name="ck_interview_questions_position"),
        CheckConstraint(
            "status IN ('awaiting_answer', 'answered', 'skipped')",
            name="ck_interview_questions_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    interview_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    main_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_question_id: Mapped[int | None] = mapped_column(Integer, index=True)
    position_no: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="awaiting_answer", nullable=False)


class InterviewAnswer(Base):
    """一道题至多一条最终回答事实。"""

    __tablename__ = "interview_answers"
    __table_args__ = (
        UniqueConstraint("interview_id", "question_id", name="uk_interview_answer"),
        Index(
            "uk_interview_answers_account_idempotency",
            "account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    interview_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InterviewFeedback(Base):
    """逐题结构化反馈。"""

    __tablename__ = "interview_feedback"
    __table_args__ = (
        Index("uk_interview_feedback_question", "interview_id", "question_id", unique=True),
        CheckConstraint("status IN ('available', 'failed')", name="ck_interview_feedback_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    interview_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="available", nullable=False)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    needs_followup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterviewSummary(Base):
    """面试完成或提前结束的总结。"""

    __tablename__ = "interview_summaries"
    __table_args__ = (
        Index("uk_interview_summary", "interview_id", unique=True),
        CheckConstraint("completion_type IN ('full', 'early', 'completed')", name="ck_interview_summaries_completion"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    interview_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    completion_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IntegrityScanRun(Base):
    """只读引用完整性巡检批次。"""

    __tablename__ = "integrity_scan_runs"
    __table_args__ = (CheckConstraint("status IN ('clean', 'findings')", name="ck_integrity_scan_runs_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=public_id, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class IntegrityScanFinding(Base):
    """巡检发现的孤儿、跨账号或删除状态异常，不自动修复。"""

    __tablename__ = "integrity_scan_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    finding_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_public_id: Mapped[str | None] = mapped_column(String(96))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
