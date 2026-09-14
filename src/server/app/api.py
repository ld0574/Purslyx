"""Purslyx 正式 API 的可运行纵向实现。

接口按已评审的 API 索引组织，当前使用本地确定性模型同步完成任务，但所有计次任务
仍会写入 Task、输入快照、outbox、预留和结算事实，后续接 Celery 时无需改变业务契约。
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import math
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Annotated, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Path as PathParam, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .budget import settle_budget
from .config import settings
from .db import get_db
from .errors import DomainError, NotFoundError
from .matching import build_match_result
from .model_provider import get_model_provider
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
    AuditEvent,
    ApplyClick,
    BudgetReservation,
    BrowserAuthCode,
    BrowserJobDraft,
    BrowserSession,
    Document,
    DocumentDraft,
    DocumentVersion,
    Export,
    Fact,
    FactVersion,
    Feedback,
    Interview,
    InterviewAnswer,
    InterviewFeedback,
    InterviewQuestion,
    InterviewSummary,
    JobPoolItem,
    LogExport,
    MetricRollup,
    ModelCall,
    OneTimeToken,
    Preference,
    PreferenceVersion,
    RateLimitBucket,
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
    TaskAttempt,
    TaskInputRef,
    TaskOutbox,
    UsageGrant,
    UsageLedger,
    UsageReservation,
    WorkflowCheckpointRef,
    WebSession,
)
from .parsing import MAX_FILE_BYTES, MAX_TEXT_CHARS, detect_source_type, extract_file_text, normalize_text, parse_job_text, sha256_bytes
from .pdf_export import render_resume_pdf
from .security import (
    browser_account,
    browser_expiry,
    current_web_account,
    hash_password,
    hash_secret,
    issue_secret,
    normalize_email,
    now_utc,
    permissions_for,
    public_account,
    require_csrf,
    require_permission,
    require_recruiter,
    require_seeker,
    require_verified,
    session_expiry,
    verify_password,
)
from .storage import atomic_write_bytes, ensure_storage_capacity, private_path, safe_download_name, storage_key
from .services import (
    FEATURES_BY_ROLE,
    available_count,
    create_task,
    fail_task,
    finish_task,
    grant_feature,
    grant_trial_if_needed,
    mark_task_running,
    model_call,
    page_rows,
    payload_hash,
    release_feature,
    run_local_task,
    settle_feature,
    shanghai_date,
    task_view,
    usage_view,
)


router = APIRouter(prefix="/api/v1")
WebAccount = Annotated[Account, Depends(current_web_account)]
BrowserAccount = Annotated[Account, Depends(browser_account)]
LOGGER = logging.getLogger("purslyx.api")

# debug 且未配置密钥时使用随机进程密钥，重启后旧的去投递令牌自然失效。
_PROCESS_TOKEN_SECRET = secrets.token_bytes(32)


def _meta(request: Request) -> dict[str, str]:
    request_id = request.headers.get("X-Request-ID", "").strip()
    if not request_id or len(request_id) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", request_id):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    return {
        "request_id": request_id,
        "server_time": now_utc().isoformat(),
    }


def _ok(request: Request, data: Any, *, code: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    meta = _meta(request)
    response = JSONResponse(status_code=code, content={"data": data, "meta": meta})
    response.headers.setdefault("X-Request-ID", meta["request_id"])
    response.headers.setdefault("Cache-Control", "private, no-store")
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def _idempotency_key(request: Request, *, required: bool = True) -> str | None:
    key = request.headers.get("Idempotency-Key", "").strip()
    if required and not key:
        raise DomainError("IDEMPOTENCY_KEY_REQUIRED", "写请求需要 Idempotency-Key", 400)
    if len(key) > 128:
        raise DomainError("IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 不能超过 128 个字符", 422)
    return key or None


def _write_guard(request: Request, account: Account, *, verified: bool = True) -> None:
    if verified:
        require_verified(account)
    require_csrf(request, account)


def _email_valid(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]{1,128}@[\w.-]{1,200}", value.strip()))


def _is_allowed_origin(value: str, allowed: list[str]) -> bool:
    """只接受精确配置的 scheme + host + port，不接受带路径的伪来源。"""

    parsed = urlparse(value)
    return (
        value in allowed
        and parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _request_origin(request: Request) -> str:
    """返回规范化的请求来源；限频键不能使用未验证的客户端自报头。"""

    origin = request.headers.get("Origin", "").strip()
    if origin:
        return origin[:512]
    return "no-origin"


def _rate_limit_key(request: Request, bucket: str, *, subject: str | None = None) -> str:
    client_host = request.client.host if request.client else "unknown"
    value = f"{bucket}:{client_host}:{_request_origin(request)}"
    if subject:
        value += f":{normalize_email(subject)[:254]}"
    # 数据库字段有固定长度；摘要也避免把邮箱直接写入限频表。
    return f"{bucket}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _rate_limit(
    db: Session,
    request: Request,
    bucket: str,
    *,
    limit: int,
    subject: str | None = None,
    window_seconds: int = 60,
) -> None:
    """用 PostgreSQL 原子 upsert 做跨进程限频，并返回可靠的 Retry-After。"""

    if limit < 1 or window_seconds < 1:
        raise RuntimeError("rate limit configuration must be positive")
    now = now_utc()
    window_start = now - timedelta(seconds=window_seconds)
    key = _rate_limit_key(request, bucket, subject=subject)
    statement = (
        pg_insert(RateLimitBucket)
        .values(
            bucket_key=key,
            window_started_at=now,
            hit_count=1,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[RateLimitBucket.bucket_key],
            set_={
                "window_started_at": case(
                    (RateLimitBucket.window_started_at < window_start, now),
                    else_=RateLimitBucket.window_started_at,
                ),
                "hit_count": case(
                    (RateLimitBucket.window_started_at < window_start, 1),
                    else_=RateLimitBucket.hit_count + 1,
                ),
                "updated_at": now,
            },
        )
        .returning(RateLimitBucket.window_started_at, RateLimitBucket.hit_count)
    )
    window_began, hit_count = db.execute(statement).one()
    # 限频命中必须在业务处理前单独提交，否则业务异常会把命中记录一并回滚。
    db.commit()
    if hit_count > limit:
        elapsed = max(0, int((now - window_began).total_seconds()))
        retry_after = max(1, window_seconds - elapsed)
        raise DomainError(
            "RATE_LIMITED",
            "请求过于频繁，请稍后再试",
            429,
            "retry_later",
            retryable=True,
            retry_after=retry_after,
        )


def _token_response(raw_token: str, csrf_token: str | None = None) -> dict[str, str]:
    result = {"access_token": raw_token, "token_type": "bearer"}
    if csrf_token:
        result["csrf_token"] = csrf_token
    return result


def _create_web_session(db: Session, account: Account) -> tuple[WebSession, str, str]:
    raw_token = issue_secret()
    csrf_token = issue_secret()
    session = WebSession(
        account_id=account.id,
        token_hash=hash_secret(raw_token),
        csrf_token_hash=hash_secret(csrf_token),
        expires_at=session_expiry(settings.session_days),
    )
    db.add(session)
    db.flush()
    return session, raw_token, csrf_token


def _create_one_time_token(db: Session, account_id: int, token_type: str) -> str:
    ttl = {
        "verify_email": timedelta(hours=settings.verification_token_hours),
        "reset_password": timedelta(minutes=settings.password_reset_minutes),
        "recover_account": timedelta(minutes=settings.account_recovery_minutes),
    }.get(token_type)
    if ttl is None:
        raise RuntimeError(f"unsupported one-time token type: {token_type}")
    # 新令牌替换同账号同类型的旧令牌，避免多个仍有效的邮件链接并存。
    db.query(OneTimeToken).filter(
        OneTimeToken.account_id == account_id,
        OneTimeToken.token_type == token_type,
        OneTimeToken.consumed_at.is_(None),
        OneTimeToken.revoked_at.is_(None),
    ).update({OneTimeToken.revoked_at: now_utc()}, synchronize_session=False)
    raw = issue_secret()
    db.add(
        OneTimeToken(
            account_id=account_id,
            token_hash=hash_secret(raw),
            token_type=token_type,
            expires_at=now_utc() + ttl,
        )
    )
    return raw


def _account_or_404(db: Session, public_id: str) -> Account:
    item = db.scalar(select(Account).where(Account.public_id == public_id))
    if item is None:
        raise NotFoundError("账号不存在")
    return item


def _document_type_is_job(value: str) -> bool:
    return value in {"job", "job_description"}


def _document(db: Session, account_id: int, public_id: str) -> Document:
    item = db.scalar(
        select(Document).where(
            Document.public_id == public_id,
            Document.account_id == account_id,
            Document.deleted_at.is_(None),
        )
    )
    if item is None:
        raise NotFoundError("资料不存在")
    return item


def _version(db: Session, account_id: int, public_id: str) -> DocumentVersion:
    item = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.public_id == public_id,
            DocumentVersion.account_id == account_id,
            DocumentVersion.deleted_at.is_(None),
        )
    )
    if item is None:
        raise NotFoundError("资料版本不存在")
    document = db.get(Document, item.document_id)
    if document is None or document.account_id != account_id or document.deleted_at is not None:
        raise NotFoundError("资料版本不存在")
    return item


def _latest_version(db: Session, document_id: int, account_id: int) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id, DocumentVersion.account_id == account_id, DocumentVersion.deleted_at.is_(None))
        .order_by(DocumentVersion.version_no.desc())
    )


def _latest_draft(db: Session, document_id: int, account_id: int) -> DocumentDraft | None:
    return db.scalar(
        select(DocumentDraft)
        .where(DocumentDraft.document_id == document_id, DocumentDraft.account_id == account_id)
        .order_by(DocumentDraft.created_at.desc())
    )


def _version_view(db: Session, version: DocumentVersion) -> dict[str, Any]:
    document = db.get(Document, version.document_id)
    return {
        "id": version.public_id,
        "document_id": document.public_id if document else None,
        "version_no": version.version_no,
        "content": version.content,
        "title": document.title if document else None,
        "source_type": document.source_type if document else None,
        "schema_version": version.schema_version,
        "confirmed_at": version.confirmed_at.isoformat(),
    }


def _document_summary(db: Session, item: Document) -> dict[str, Any]:
    draft = _latest_draft(db, item.id, item.account_id)
    version = _latest_version(db, item.id, item.account_id)
    return {
        "id": item.public_id,
        "document_type": "job_description" if item.document_type == "job" else item.document_type,
        "subject_type": item.subject_type,
        "title": item.title,
        "status": item.status,
        "source_type": item.source_type,
        "latest_draft": (
            {
                "id": draft.public_id,
                "status": draft.status,
                "revision": draft.revision,
                "missing_field_codes": draft.missing_field_codes or [],
                "failure_code": draft.failure_code,
                "expires_at": draft.expires_at.isoformat() if draft.expires_at else None,
            }
            if draft
            else None
        ),
        "latest_version": (
            {
                "id": version.public_id,
                "version_no": version.version_no,
                "schema_version": version.schema_version,
                "confirmed_at": version.confirmed_at.isoformat(),
            }
            if version
            else None
        ),
        "revision": item.draft_revision,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _document_detail(db: Session, item: Document, version_id: str | None = None) -> dict[str, Any]:
    selected = _version(db, item.account_id, version_id) if version_id else _latest_version(db, item.id, item.account_id)
    if selected is not None and selected.document_id != item.id:
        raise NotFoundError("资料版本不属于当前资料")
    versions = db.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == item.id, DocumentVersion.deleted_at.is_(None)).order_by(DocumentVersion.version_no.desc())
    ).all()
    draft = _latest_draft(db, item.id, item.account_id)
    result = _document_summary(db, item)
    result.update(
        {
            "draft_content": draft.content if draft and draft.status != "expired" else None,
            "version": _version_view(db, selected) if selected else None,
            "versions": [_version_view(db, row) for row in versions],
        }
    )
    return result


def _require_deletion_match(request: Request, impact_version: str) -> None:
    """删除必须基于用户刚刚看到的影响快照，避免误删新增关联内容。"""

    supplied = request.headers.get("If-Match", "").strip()
    if supplied.startswith("W/"):
        supplied = supplied[2:].strip()
    supplied = supplied.strip('"')
    if not supplied:
        raise DomainError(
            "RESOURCE_DELETION_CONFIRMATION_REQUIRED",
            "请先读取删除影响并确认后再删除",
            409,
            "confirm_deletion",
        )
    if supplied != impact_version:
        raise DomainError(
            "RESOURCE_DELETION_CHANGED",
            "删除影响已经变化，请刷新后重新确认",
            409,
            "refresh",
        )


def _no_content(request: Request) -> Response:
    """统一返回删除／撤销后的无正文响应。"""

    meta = _meta(request)
    return Response(
        status_code=204,
        headers={"X-Request-ID": meta["request_id"], "Cache-Control": "private, no-store"},
    )


def _task_references(task: Task, resource_ids: set[str]) -> bool:
    """在任务冻结输入中查找资源，避免用 JSON SQL 表达式绕过版本边界。"""

    data = task.input_data if isinstance(task.input_data, dict) else {}
    direct_keys = (
        "document_id",
        "resume_version_id",
        "job_version_id",
        "preference_version_id",
        "analysis_id",
        "rewrite_id",
        "interview_id",
        "job_pool_item_id",
        "resume_variant_version_id",
    )
    if any(str(data.get(key)) in resource_ids for key in direct_keys if data.get(key)):
        return True
    for value in data.get("fact_version_ids") or []:
        if str(value) in resource_ids:
            return True
    for value in data.get("input_versions") or []:
        if isinstance(value, dict) and str(value.get("resource_id")) in resource_ids:
            return True
    return False


def _mark_tasks_cancelled(
    db: Session,
    account_id: int,
    *,
    resource_ids: set[str],
    task_ids: set[int] | None = None,
    reason: str,
) -> set[int]:
    """立即撤销任务可见性并释放仍未结算的功能次数。"""

    candidates = db.scalars(
        select(Task).where(
            Task.account_id == account_id,
            Task.status.in_(["queued", "running", "retry_wait"]),
            Task.deleted_at.is_(None),
        )
    ).all()
    selected = {
        task.id
        for task in candidates
        if (task_ids and task.id in task_ids) or _task_references(task, resource_ids)
    }
    selected_tasks = [task for task in candidates if task.id in selected]
    revoked_at = now_utc()
    for task in selected_tasks:
        task.status = "cancelled"
        task.current_step = "cancelled"
        task.failure = {"code": "TASK_SOURCE_DELETED", "message": reason, "retryable": False}
        task.completed_at = revoked_at
        for attempt in db.scalars(
            select(TaskAttempt).where(TaskAttempt.task_id == task.id, TaskAttempt.status.in_(["queued", "running"]))
        ).all():
            attempt.status = "cancelled"
            attempt.error_code = "TASK_SOURCE_DELETED"
            attempt.finished_at = revoked_at
        reservation = db.scalar(
            select(UsageReservation).where(
                UsageReservation.task_id == task.id,
                UsageReservation.status == "reserved",
            )
        )
        if reservation is not None:
            release_feature(db, reservation.id, reason)
        # 删除期间若外部模型已经发出，预算只能按未知成本持有；本地模型上界为零。
        for budget in db.scalars(
            select(BudgetReservation).where(
                BudgetReservation.task_id == task.id,
                BudgetReservation.status == "reserved",
            )
        ).all():
            settle_budget(db, budget.id, None)
        db.query(TaskOutbox).filter(
            TaskOutbox.task_id == task.id,
            TaskOutbox.status.in_(["pending", "claimed"]),
        ).update(
            {
                TaskOutbox.status: "published",
                TaskOutbox.claimed_by: None,
                TaskOutbox.claimed_at: None,
                TaskOutbox.published_at: revoked_at,
            },
            synchronize_session=False,
        )
        db.query(WorkflowCheckpointRef).filter(
            WorkflowCheckpointRef.task_id == task.id,
            WorkflowCheckpointRef.content_access_revoked_at.is_(None),
        ).update({WorkflowCheckpointRef.content_access_revoked_at: revoked_at}, synchronize_session=False)
    db.flush()
    return selected


def _mark_files_deleted(
    db: Session,
    account_id: int,
    *,
    storage_keys: set[str],
    purposes: set[str] | None = None,
) -> list[Path]:
    """先标记私有文件，再返回事务提交后可安全删除的精确路径。"""

    if not storage_keys:
        return []
    conditions = [
        StoredFile.account_id == account_id,
        StoredFile.status == "available",
        StoredFile.deleted_at.is_(None),
    ]
    if purposes:
        conditions.append(StoredFile.purpose.in_(purposes))
    if storage_keys:
        conditions.append(StoredFile.storage_key.in_(storage_keys))
    rows = db.scalars(select(StoredFile).where(*conditions)).all()
    paths: list[Path] = []
    for row in rows:
        paths.append(private_path(row.storage_key, settings.data_dir))
        row.status = "deleted"
        row.deleted_at = now_utc()
    db.flush()
    return list(dict.fromkeys(paths))


def _unlink_after_commit(paths: list[Path]) -> None:
    """只清理已通过私有根目录校验的文件；缺失文件视为已清理。"""

    for path in paths:
        path.unlink(missing_ok=True)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    registration_role: Literal["seeker", "recruiter"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=256)


class PasswordResetRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=128)


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)


class BrowserCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str = Field(min_length=1, max_length=512)
    nonce: str = Field(min_length=32, max_length=128)


class BrowserExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # code 保留兼容旧版脚本；新客户端可使用文档中的 authorization_code。
    code: str | None = Field(default=None, min_length=16, max_length=256)
    authorization_code: str | None = Field(default=None, min_length=16, max_length=256)
    origin: str = Field(min_length=1, max_length=512)
    nonce: str | None = Field(default=None, min_length=32, max_length=128)

    @model_validator(mode="after")
    def code_must_be_present(self) -> "BrowserExchangeRequest":
        if not self.authorization_code and not self.code:
            raise ValueError("需要授权码")
        return self


class DocumentJSONRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_type: Literal["resume", "job_description"]
    subject_type: Literal["self_resume", "candidate_resume", "job_description"]
    title: str = Field(default="未命名资料", min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = normalize_text(value)
        if not value:
            raise ValueError("资料正文不能为空")
        return value


class DocumentUploadMeta(BaseModel):
    """multipart 文件上传的元字段，与 JSON 入口保持相同的基础校验。"""

    model_config = ConfigDict(extra="forbid")
    document_type: Literal["resume", "job_description"]
    subject_type: Literal["self_resume", "candidate_resume", "job_description"]
    title: str = Field(default="未命名资料", min_length=1, max_length=160)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str = Field(min_length=1, max_length=36)
    base_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=160)
    content: dict[str, Any]


class PreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=160)
    context: Literal["self", "candidate"] = "self"
    subject_document_id: str | None = None
    is_default: bool = False
    preference: dict[str, Any]


class PreferenceUpdateRequest(PreferenceRequest):
    base_revision: int = Field(ge=1)
    status: Literal["active", "archived"] = "active"


class FactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    source_document_version_id: str | None = None
    fact_category: str = Field(min_length=1, max_length=40)
    fact_text: str = Field(min_length=1, max_length=2000)
    source_segment_key: str | None = Field(default=None, max_length=96)
    source_type: str = Field(default="user_added", max_length=32)


class FactVersionRequest(FactRequest):
    base_revision: int = Field(ge=1)


class RewriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: str
    source_resume_version_id: str
    segment_keys: list[str] = Field(min_length=1, max_length=20)
    fact_version_ids: list[str] = Field(default_factory=list, max_length=20)
    confirm_usage: bool


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["adopt", "keep_original", "revert", "adopted", "rejected", "edited"]
    edited_text: str | None = Field(default=None, max_length=5000)
    base_decision_no: int = Field(default=0, ge=0)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_pool_item_id: str
    source_resume_version_id: str
    title: str = Field(min_length=1, max_length=160)
    rewrite_id: str | None = None


class ResumeVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1)
    content: dict[str, Any]
    layout: dict[str, Any] = Field(default_factory=dict)
    template_version: Literal["resume-template-v1"] = "resume-template-v1"
    rewrite_id: str | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_variant_version_id: str


# ------------------------------ 用户与浏览器授权 ------------------------------


@router.post("/auth/register", tags=["auth"], status_code=202)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """注册时固定身份；身份后续不能通过接口切换。"""

    email = payload.email.strip()
    normalized = normalize_email(email)
    _rate_limit(db, request, "auth.register", limit=5, subject=email)
    if not _email_valid(email):
        raise DomainError("AUTH_EMAIL_INVALID", "请输入有效邮箱", 422)
    existing = db.scalar(select(Account).where(Account.email_normalized == normalized))
    if existing is not None:
        data: dict[str, Any] = {
            "status": "verification_requested",
            "message": "如果该邮箱可以注册，我们会发送验证邮件。",
        }
        if existing.email_verified_at is None and settings.debug:
            data["verification_token"] = _create_one_time_token(db, existing.id, "verify_email")
            db.commit()
        return _ok(request, data, code=202)
    account = Account(
        email=email,
        email_normalized=normalized,
        password_hash=hash_password(payload.password),
        registration_role=payload.registration_role,
        status="active" if settings.auto_verify_local else "pending_verification",
        email_verified_at=now_utc() if settings.auto_verify_local else None,
    )
    db.add(account)
    db.flush()
    verification_token = None
    if account.email_verified_at is None:
        verification_token = _create_one_time_token(db, account.id, "verify_email")
    grants = grant_trial_if_needed(db, account) if settings.auto_verify_local else []
    db.commit()
    data: dict[str, Any] = {
        "status": "verification_requested",
        "message": "如果该邮箱可以注册，我们会发送验证邮件。",
    }
    # 只在本地 debug 返回一次性 token，线上由邮件服务发送，避免 token 进入业务日志。
    if settings.debug and verification_token:
        data["verification_token"] = verification_token
    # 本地自动验证仍然发放试用次数，但不把账号投影带入公开注册响应。
    _ = grants
    return _ok(request, data, code=202)


@router.post("/auth/verify-email", tags=["auth"])
def verify_email(payload: TokenRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _rate_limit(db, request, "auth.verify_email", limit=10, subject=payload.token)
    token = db.scalar(
        select(OneTimeToken).where(
            OneTimeToken.token_hash == hash_secret(payload.token),
            OneTimeToken.token_type == "verify_email",
        ).with_for_update()
    )
    if token is None or token.revoked_at or token.expires_at <= now_utc():
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "验证链接无效或已过期", 410)
    account = db.get(Account, token.account_id)
    if account is None:
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "验证链接无效或已过期", 410)
    if token.consumed_at:
        if account.email_verified_at is not None:
            return _ok(request, {"account": public_account(db, account), "trial_grants": []})
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "验证链接无效或已过期", 410)
    token.consumed_at = now_utc()
    account.email_verified_at = now_utc()
    account.status = "active"
    grants = grant_trial_if_needed(db, account)
    db.commit()
    return _ok(
        request,
        {
            "account": public_account(db, account),
            "trial_grants": [{"feature": item.feature, "count": item.count} for item in grants],
        },
    )


@router.post("/auth/resend-verification", tags=["auth"])
def resend_verification(payload: RecoveryRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """返回中性文案；debug 环境附带 token 便于本机演示。"""

    _rate_limit(db, request, "auth.resend_verification", limit=5, subject=payload.email)
    account = db.scalar(select(Account).where(Account.email_normalized == normalize_email(payload.email)))
    data: dict[str, Any] = {"accepted": True, "message": "如果账号存在，验证邮件将发送到注册邮箱。"}
    if account and account.email_verified_at is None:
        token = _create_one_time_token(db, account.id, "verify_email")
        if settings.debug:
            data["verification_token"] = token
        db.commit()
    return _ok(request, {"status": "verification_requested", **data}, code=202)


@router.post("/auth/login", tags=["auth"])
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _rate_limit(db, request, "auth.login", limit=10, subject=payload.email)
    account = db.scalar(select(Account).where(Account.email_normalized == normalize_email(payload.email)))
    if account is None or not verify_password(account.password_hash, payload.password):
        db.add(SecurityEvent(event_type="login", outcome="failed", reason_code="invalid_credentials", client_type="web"))
        db.commit()
        raise DomainError("AUTH_INVALID_CREDENTIALS", "邮箱或密码不正确", 401)
    if account.status == "suspended":
        raise DomainError("AUTH_ACCOUNT_SUSPENDED", "账号已暂停", 403)
    if account.email_verified_at is None:
        raise DomainError("AUTH_EMAIL_UNVERIFIED", "请先完成邮箱验证", 403, "verify_email")
    _, raw, csrf = _create_web_session(db, account)
    account.last_login_at = now_utc()
    db.add(SecurityEvent(account_id=account.id, event_type="login", outcome="succeeded", client_type="web"))
    db.add(SecurityEvent(account_id=account.id, event_type="web_session_issued", outcome="succeeded", client_type="web"))
    db.commit()
    response = _ok(request, {"account": public_account(db, account), **_token_response(raw, csrf)})
    response.set_cookie(
        "purslyx_session",
        raw,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_days * 86400,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/auth/logout", tags=["auth"])
def logout(request: Request, account: WebAccount, db: Session = Depends(get_db)) -> Response:
    require_csrf(request, account)
    session: WebSession | None = getattr(request.state, "web_session", None)
    if session:
        session.revoked_at = now_utc()
        db.add(SecurityEvent(account_id=account.id, event_type="web_session_revoked", outcome="succeeded", client_type="web"))
    db.commit()
    response = _no_content(request)
    response.delete_cookie("purslyx_session")
    return response


@router.post("/auth/forgot-password", tags=["auth"])
def forgot_password(payload: RecoveryRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _rate_limit(db, request, "auth.forgot_password", limit=5, subject=payload.email)
    account = db.scalar(select(Account).where(Account.email_normalized == normalize_email(payload.email)))
    data: dict[str, Any] = {"accepted": True, "message": "如果账号存在，重置邮件将发送到注册邮箱。"}
    if account:
        token = _create_one_time_token(db, account.id, "reset_password")
        if settings.debug:
            data["reset_token"] = token
        db.commit()
    return _ok(request, {"status": "reset_requested", **data}, code=202)


@router.post("/auth/reset-password", tags=["auth"])
def reset_password(payload: PasswordResetRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _rate_limit(db, request, "auth.reset_password", limit=10, subject=payload.token)
    token = db.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == hash_secret(payload.token), OneTimeToken.token_type == "reset_password").with_for_update())
    if token is None or token.consumed_at or token.revoked_at or token.expires_at <= now_utc():
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "重置链接无效或已过期", 410)
    account = db.get(Account, token.account_id)
    if account is None:
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "重置链接无效或已过期", 410)
    account.password_hash = hash_password(payload.new_password)
    token.consumed_at = now_utc()
    db.query(WebSession).filter(WebSession.account_id == account.id, WebSession.revoked_at.is_(None)).update({WebSession.revoked_at: now_utc()})
    db.add(SecurityEvent(account_id=account.id, event_type="password_reset", outcome="succeeded", client_type="web"))
    db.commit()
    return _no_content(request)


@router.post("/auth/request-account-recovery", tags=["auth"])
def request_account_recovery(payload: RecoveryRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _rate_limit(db, request, "auth.request_account_recovery", limit=5, subject=payload.email)
    account = db.scalar(select(Account).where(Account.email_normalized == normalize_email(payload.email)))
    data: dict[str, Any] = {"accepted": True, "message": "如果账号存在，将发送账号恢复说明。"}
    if account and account.status == "suspended":
        token = _create_one_time_token(db, account.id, "recover_account")
        if settings.debug:
            data["recovery_token"] = token
        db.commit()
    return _ok(request, {"status": "recovery_requested", **data}, code=202)


@router.post("/auth/recover-account", tags=["auth"])
def recover_account(payload: TokenRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _rate_limit(db, request, "auth.recover_account", limit=10, subject=payload.token)
    token = db.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == hash_secret(payload.token), OneTimeToken.token_type == "recover_account").with_for_update())
    if token is None or token.revoked_at or token.expires_at <= now_utc():
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "恢复链接无效或已过期", 410)
    account = db.get(Account, token.account_id)
    if account is None:
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "恢复链接无效或已过期", 410)
    if token.consumed_at:
        return _no_content(request)
    if account.status != "suspended":
        raise DomainError("AUTH_TOKEN_INVALID_OR_EXPIRED", "恢复链接无效或已过期", 410)
    token.consumed_at = now_utc()
    account.status = "active"
    account.revision += 1
    db.add(SecurityEvent(account_id=account.id, event_type="account_recovered", outcome="succeeded", client_type="web"))
    db.commit()
    return _no_content(request)


@router.get("/me", tags=["auth"])
def me(request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    return _ok(request, {"account": public_account(db, account)})


@router.post("/auth/browser-codes", tags=["browser-auth"])
def create_browser_code(payload: BrowserCodeRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    if not _is_allowed_origin(payload.origin, settings.browser_origins):
        raise DomainError("BROWSER_ORIGIN_INVALID", "浏览器来源不在允许范围内", 422)
    raw = issue_secret()
    expires_at = now_utc() + timedelta(minutes=settings.browser_auth_code_minutes)
    db.add(BrowserAuthCode(account_id=account.id, code_hash=hash_secret(raw), origin=payload.origin, nonce=payload.nonce, expires_at=expires_at))
    db.commit()
    return _ok(
        request,
        {
            "authorization_code": raw,
            # 保留旧脚本字段，迁移期间新客户端应使用 authorization_code。
            "code": raw,
            "nonce": payload.nonce,
            "expires_at": expires_at.isoformat(),
        },
        code=201,
    )


@router.post("/browser-auth/exchange", tags=["browser-auth"])
def exchange_browser_code(payload: BrowserExchangeRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    _rate_limit(db, request, "auth.browser_exchange", limit=10)
    request_origin = request.headers.get("Origin", "").strip()
    if request_origin and request_origin != payload.origin:
        raise DomainError("BROWSER_ORIGIN_INVALID", "浏览器来源不匹配", 403)
    if not _is_allowed_origin(payload.origin, settings.browser_origins) or not payload.nonce:
        raise DomainError("BROWSER_ORIGIN_INVALID", "浏览器来源或 nonce 不合法", 403)
    raw_code = payload.authorization_code or payload.code or ""
    code = db.scalar(select(BrowserAuthCode).where(BrowserAuthCode.code_hash == hash_secret(raw_code)).with_for_update())
    if code is None or code.consumed_at or code.expires_at <= now_utc() or code.origin != payload.origin or code.nonce != payload.nonce:
        raise DomainError("BROWSER_CODE_INVALID", "浏览器授权码无效或已过期", 401)
    account = db.get(Account, code.account_id)
    if account is None or account.status != "active" or account.email_verified_at is None:
        raise DomainError("AUTH_EMAIL_UNVERIFIED", "账号尚未完成邮箱验证", 403)
    code.consumed_at = now_utc()
    raw = issue_secret()
    expires_at = browser_expiry(settings.browser_session_hours)
    db.add(BrowserSession(account_id=account.id, token_hash=hash_secret(raw), expires_at=expires_at))
    db.add(SecurityEvent(account_id=account.id, event_type="browser_session_issued", outcome="succeeded", client_type="browser"))
    db.commit()
    return _ok(request, {"browser_token": raw, "token_type": "Bearer", "scope": ["job_drafts.create", "job_drafts.read_own", "browser_session.revoke_self"], "scope_version": 1, "expires_at": expires_at.isoformat()}, code=201)


@router.delete("/browser-auth/session", tags=["browser-auth"])
def delete_browser_session(request: Request, account: BrowserAccount, db: Session = Depends(get_db)) -> JSONResponse:
    session: BrowserSession | None = getattr(request.state, "browser_session", None)
    if session:
        session.revoked_at = now_utc()
        db.add(SecurityEvent(account_id=account.id, event_type="browser_session_revoked", outcome="succeeded", client_type="browser"))
    db.commit()
    return _no_content(request)


# ------------------------------------ 简历 ------------------------------------


async def _read_document_request(request: Request) -> tuple[dict[str, Any], str, bytes | None, str | None]:
    """同时支持 JSON 文本和 multipart 文件，统一交给同一套解析逻辑。"""

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        metadata = DocumentUploadMeta.model_validate(
            {
                "document_type": form.get("document_type", ""),
                "subject_type": form.get("subject_type", ""),
                "title": form.get("title") or "未命名资料",
            }
        )
        document_type = metadata.document_type
        subject_type = metadata.subject_type
        title = metadata.title
        text_value = str(form.get("text") or "").strip()
        file_value = form.get("file")
        if bool(text_value) == bool(file_value):
            raise DomainError("DOCUMENT_INPUT_INVALID", "text 和 file 必须二选一", 422)
        if file_value:
            filename = getattr(file_value, "filename", None)
            content = await file_value.read()
            source_type = detect_source_type(filename, getattr(file_value, "content_type", None))
            return {"document_type": document_type, "subject_type": subject_type, "title": title}, source_type, content, filename
        parsed = DocumentJSONRequest.model_validate(
            {"document_type": document_type, "subject_type": subject_type, "title": title, "text": text_value}
        )
        return parsed.model_dump(), "text", None, None
    try:
        data = await request.json()
    except Exception as exc:
        raise DomainError("REQUEST_INVALID", "请求体不是有效 JSON", 422) from exc
    # 这里用局部模型校验，避免 multipart 与 JSON 的契约分叉。
    parsed = DocumentJSONRequest.model_validate(data)
    return parsed.model_dump(), "text", None, None


def _validate_subject(account: Account, document_type: str, subject_type: str) -> None:
    if document_type not in {"resume", "job_description"}:
        raise DomainError("DOCUMENT_TYPE_INVALID", "资料类型不受支持", 422)
    if document_type == "resume" and subject_type not in {"self_resume", "candidate_resume"}:
        raise DomainError("DOCUMENT_SUBJECT_INVALID", "简历资料的 subject_type 不合法", 422)
    if document_type == "job_description" and subject_type != "job_description":
        raise DomainError("DOCUMENT_SUBJECT_INVALID", "岗位资料的 subject_type 不合法", 422)
    if account.registration_role == "seeker" and subject_type == "candidate_resume":
        raise DomainError("DOCUMENT_ROLE_MISMATCH", "求职账号不能创建候选人简历", 403)
    if account.registration_role == "recruiter" and subject_type == "self_resume":
        raise DomainError("DOCUMENT_ROLE_MISMATCH", "招聘账号不能创建求职者简历", 403)


def _missing_document_fields(content: dict[str, Any], document_type: str) -> list[str]:
    if document_type == "resume":
        return ["resume_experience"] if not any(section.get("segments") for section in content.get("sections", [])) else []
    fields = content.get("job_fields") or {}
    missing = []
    for key, code in (("title", "job_title"), ("requirements", "job_requirements"), ("locations", "job_location"), ("salary", "job_salary")):
        if not fields.get(key):
            missing.append(code)
    return missing


def _validate_document_content(content: dict[str, Any], document_type: str) -> None:
    """校验确认版本的最小结构，避免任意 JSON 绕过解析约定。"""

    if not isinstance(content, dict) or content.get("schema_version", "document-content-v1") != "document-content-v1":
        raise DomainError("DOCUMENT_SCHEMA_INVALID", "资料内容 Schema 版本不受支持", 422)
    sections = content.get("sections", [])
    if not isinstance(sections, list):
        raise DomainError("DOCUMENT_SCHEMA_INVALID", "sections 必须是数组", 422)
    section_keys: set[str] = set()
    segment_keys: set[str] = set()
    for position, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise DomainError("DOCUMENT_SCHEMA_INVALID", "资料分段结构不合法", 422)
        key = str(section.get("section_key", ""))
        if not key or len(key) > 96 or key in section_keys:
            raise DomainError("DOCUMENT_SCHEMA_INVALID", "资料分段标识必须唯一且不超过 96 个字符", 422)
        section_keys.add(key)
        if int(section.get("position", position)) != position:
            raise DomainError("DOCUMENT_SCHEMA_INVALID", "资料分段 position 必须从 1 连续递增", 422)
        segments = section.get("segments", [])
        if not isinstance(segments, list):
            raise DomainError("DOCUMENT_SCHEMA_INVALID", "segments 必须是数组", 422)
        for segment in segments:
            if not isinstance(segment, dict) or not str(segment.get("segment_key", "")):
                raise DomainError("DOCUMENT_SCHEMA_INVALID", "资料段落缺少 segment_key", 422)
            segment_key = str(segment["segment_key"])
            if len(segment_key) > 96 or segment_key in segment_keys:
                raise DomainError("DOCUMENT_SCHEMA_INVALID", "segment_key 必须唯一且不超过 96 个字符", 422)
            text_value = normalize_text(str(segment.get("text", "")))
            if not text_value or len(text_value) > MAX_TEXT_CHARS:
                raise DomainError("DOCUMENT_SCHEMA_INVALID", "资料段落正文不能为空且不能超过长度限制", 422)
            segment_keys.add(segment_key)
    if document_type == "resume" and content.get("job_fields") not in (None, {}):
        raise DomainError("DOCUMENT_SCHEMA_INVALID", "简历版本不能包含岗位字段", 422)
    if document_type == "job_description" and content.get("job_fields") is not None and not isinstance(content.get("job_fields"), dict):
        raise DomainError("DOCUMENT_SCHEMA_INVALID", "岗位字段必须是对象", 422)


@router.post("/documents", tags=["documents"])
async def create_document_form(request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    key = _idempotency_key(request)
    raw_data, source_type, file_bytes, original_filename = await _read_document_request(request)
    document_type = str(raw_data.get("document_type", ""))
    subject_type = str(raw_data.get("subject_type", ""))
    _validate_subject(account, document_type, subject_type)
    if file_bytes is not None:
        if len(file_bytes) > MAX_FILE_BYTES:
            raise DomainError("DOCUMENT_TOO_LARGE", "文件不能超过 20 MiB", 413)
        text_value = None
        request_digest = payload_hash({**raw_data, "file_sha256": sha256_bytes(file_bytes)})
    else:
        text_value = normalize_text(str(raw_data.get("text", "")))
        if not text_value:
            raise DomainError("DOCUMENT_CONTENT_EMPTY", "资料正文不能为空", 422)
        if len(text_value) > MAX_TEXT_CHARS:
            raise DomainError("DOCUMENT_TOO_LARGE", "正文不能超过 100,000 个字符", 413)
        request_digest = payload_hash({**raw_data, "text": text_value})
    if key:
        previous = db.scalar(select(Document).where(Document.account_id == account.id, Document.idempotency_key == key, Document.deleted_at.is_(None)))
        if previous is not None:
            if previous.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的资料内容不同", 409)
            return _ok(request, _document_summary(db, previous))

    stored_path: Path | None = None
    stored_hash: str | None = None
    try:
        if file_bytes is not None:
            extension = {"pdf": ".pdf", "doc": ".doc", "docx": ".docx", "text": ".txt"}[source_type]
            stored_hash = sha256_bytes(file_bytes)
            ensure_storage_capacity(db, len(file_bytes))
            stored_path = atomic_write_bytes(settings.file_dir, file_bytes, extension)
            text_value = extract_file_text(stored_path, source_type)
        provider = get_model_provider()
        model_result = provider.extract_resume(text_value) if document_type == "resume" else provider.extract_job(text_value)
        document = Document(
            account_id=account.id,
            document_type=document_type,
            subject_type=subject_type,
            title=str(raw_data.get("title") or "未命名资料").strip() or "未命名资料",
            source_type=source_type,
            status="available",
            raw_text=text_value,
            file_path=storage_key(stored_path, settings.data_dir) if stored_path else None,
            file_sha256=stored_hash,
            idempotency_key=key,
            request_hash=request_digest,
            draft_content=model_result.value,
        )
        db.add(document)
        db.flush()
        if stored_path is not None and stored_hash is not None:
            db.add(
                StoredFile(
                    account_id=account.id,
                    purpose="document_source",
                    original_filename=original_filename,
                    media_type=(
                        {
                            "pdf": "application/pdf",
                            "doc": "application/msword",
                            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "text": "text/plain",
                        }.get(source_type, "application/octet-stream")
                    ),
                    byte_size=len(file_bytes or b""),
                    sha256=stored_hash,
                    storage_key=storage_key(stored_path, settings.data_dir),
                    status="available",
                )
            )
        draft = DocumentDraft(
            account_id=account.id,
            document_id=document.id,
            content=model_result.value,
            missing_field_codes=_missing_document_fields(model_result.value, document_type),
            status="unconfirmed",
            expires_at=now_utc() + timedelta(days=7),
        )
        db.add(draft)
        db.commit()
        db.refresh(document)
        return _ok(request, _document_summary(db, document), code=201)
    except Exception:
        db.rollback()
        if stored_path and stored_path.exists():
            stored_path.unlink(missing_ok=True)
        raise


@router.post("/documents/{document_id}/parse", tags=["documents"])
def parse_document(
    request: Request,
    account: WebAccount,
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _write_guard(request, account)
    key = _idempotency_key(request)
    document = _document(db, account.id, document_id)
    task, _, existed = create_task(
        db,
        account,
        "document_parse",
        {"document_id": document.public_id},
        idempotency_key=key,
        input_refs=[("document", document.public_id, None, document.request_hash)],
    )
    if not existed:
        db.commit()
        task_result: dict[str, Any] = {}

        def work() -> Any:
            content_text = document.raw_text
            if not content_text and document.file_path:
                content_text = extract_file_text(private_path(document.file_path, settings.data_dir), document.source_type)
            if not content_text:
                raise DomainError("DOCUMENT_CONTENT_UNREADABLE", "原始资料无法读取", 422, "paste_text")
            provider = get_model_provider()
            return provider.extract_resume(content_text) if document.document_type == "resume" else provider.extract_job(content_text)

        def save_result(value: dict[str, Any]) -> None:
            draft = _latest_draft(db, document.id, account.id)
            if draft is None:
                draft = DocumentDraft(account_id=account.id, document_id=document.id, revision=0)
                db.add(draft)
            draft.content = value
            draft.revision = (draft.revision or 0) + 1
            draft.status = "unconfirmed"
            draft.missing_field_codes = _missing_document_fields(value, document.document_type)
            document.draft_content = value
            document.draft_revision = draft.revision
            document.status = "available"
            task_result.update({"document_id": document.public_id, "draft_id": draft.public_id})

        try:
            run_local_task(
                db,
                task,
                None,
                work,
                on_success=save_result,
                cost_feature="document_parse",
                task_result=task_result,
            )
        except Exception as exc:
            db.refresh(document)
            document.status = "failed"
            document.failure_code = getattr(exc, "code", "DOCUMENT_PARSE_FAILED")
            db.commit()
            raise
    db.refresh(task)
    return _ok(request, {"task": task_view(task), "document": _document_summary(db, document)}, code=202)


@router.get("/documents", tags=["documents"])
def list_documents(
    request: Request,
    account: WebAccount,
    document_type: str | None = Query(default=None),
    subject_type: str | None = Query(default=None),
    document_status: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    statement = select(Document).where(Document.account_id == account.id, Document.deleted_at.is_(None))
    if document_type:
        statement = statement.where(Document.document_type.in_([document_type, "job" if document_type == "job_description" else document_type]))
    if subject_type:
        statement = statement.where(Document.subject_type == subject_type)
    if document_status:
        statement = statement.where(Document.status == document_status)
    rows, page = page_rows(db, statement, Document, cursor=cursor, limit=limit, timestamp_field="updated_at")
    return _ok(request, {"items": [_document_summary(db, item) for item in rows], "page": page})


@router.get("/documents/{document_id}", tags=["documents"])
def get_document(
    request: Request,
    account: WebAccount,
    document_id: str = PathParam(min_length=1, max_length=36),
    version_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return _ok(request, _document_detail(db, _document(db, account.id, document_id), version_id))


@router.post("/documents/{document_id}/versions", tags=["documents"])
def create_document_version(
    payload: VersionRequest,
    request: Request,
    account: WebAccount,
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _write_guard(request, account)
    key = _idempotency_key(request)
    document = _document(db, account.id, document_id)
    request_digest = payload_hash({"document_id": document.public_id, **payload.model_dump(mode="json")})
    if key:
        existing_by_key = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.account_id == account.id,
                DocumentVersion.idempotency_key == key,
            )
        )
        if existing_by_key is not None:
            if existing_by_key.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的版本内容不同", 409)
            return _ok(request, _version_view(db, existing_by_key))
    draft = db.scalar(select(DocumentDraft).where(DocumentDraft.public_id == payload.draft_id, DocumentDraft.document_id == document.id, DocumentDraft.account_id == account.id))
    if draft is None:
        raise NotFoundError("资料草稿不存在")
    if payload.base_revision != document.draft_revision:
        raise DomainError("DOCUMENT_REVISION_CONFLICT", "资料已发生变化，请刷新后确认", 409, "refresh")
    if draft.status == "confirmed":
        raise DomainError("DOCUMENT_DRAFT_ALREADY_CONFIRMED", "该草稿已经确认，不能重复确认", 409)
    _validate_document_content(payload.content, document.document_type)
    content = dict(payload.content)
    content.setdefault("schema_version", "document-content-v1")
    current_max = db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == document.id)) or 0
    version = DocumentVersion(account_id=account.id, document_id=document.id, version_no=int(current_max) + 1, content=content, idempotency_key=key, request_hash=request_digest)
    db.add(version)
    draft.status = "confirmed"
    draft.confirmed_at = now_utc()
    document.status = "confirmed"
    if payload.title:
        document.title = payload.title.strip() or document.title
    db.commit()
    db.refresh(version)
    return _ok(request, _version_view(db, version), code=201)


@router.get("/documents/{document_id}/file", tags=["documents"])
def get_document_file(
    request: Request,
    account: WebAccount,
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> Response:
    item = _document(db, account.id, document_id)
    if not item.file_path:
        raise NotFoundError("资料没有可下载的原始文件")
    file_path = private_path(item.file_path, settings.data_dir)
    if not file_path.is_file():
        raise NotFoundError("资料没有可下载的原始文件")
    stored = db.scalar(
        select(StoredFile).where(
            StoredFile.account_id == account.id,
            StoredFile.purpose == "document_source",
            StoredFile.storage_key == storage_key(file_path, settings.data_dir),
            StoredFile.status == "available",
            StoredFile.deleted_at.is_(None),
        )
    )
    fallback_name = f"{item.public_id}.{item.source_type if item.source_type != 'text' else 'txt'}"
    filename = safe_download_name(stored.original_filename if stored else None, fallback_name)
    media_type = stored.media_type if stored else "application/octet-stream"
    response = FileResponse(file_path, filename=filename, media_type=media_type)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _impact_version(*values: Any) -> str:
    return hashlib.sha256("|".join(str(value) for value in values).encode("utf-8")).hexdigest()[:24]


@router.get("/documents/{document_id}/deletion-impact", tags=["documents"])
def document_deletion_impact(request: Request, account: WebAccount, document_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    item = _document(db, account.id, document_id)
    version_count = db.scalar(select(func.count(DocumentVersion.id)).where(DocumentVersion.document_id == item.id, DocumentVersion.deleted_at.is_(None))) or 0
    analysis_count = db.scalar(select(func.count(Analysis.id)).where(Analysis.account_id == account.id, Analysis.deleted_at.is_(None), (Analysis.resume_version_id.in_(select(DocumentVersion.id).where(DocumentVersion.document_id == item.id)) | Analysis.job_version_id.in_(select(DocumentVersion.id).where(DocumentVersion.document_id == item.id))))) or 0
    running_count = db.scalar(select(func.count(Task.id)).where(Task.account_id == account.id, Task.status.in_(["queued", "running"]))) or 0
    version = _impact_version(item.public_id, item.updated_at.isoformat(), version_count, analysis_count, running_count)
    return _ok(request, {"resource_id": item.public_id, "resource_type": "document", "affected": {"versions": version_count, "analyses": analysis_count, "running_tasks": running_count}, "impact_version": version}, headers={"ETag": f'"{version}"'})


@router.delete("/documents/{document_id}", tags=["documents"])
def delete_document(request: Request, account: WebAccount, document_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    item = _document(db, account.id, document_id)
    version_rows = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == item.id, DocumentVersion.deleted_at.is_(None))).all()
    version_ids = {row.id for row in version_rows}
    analysis_rows = db.scalars(
        select(Analysis).where(
            Analysis.account_id == account.id,
            Analysis.deleted_at.is_(None),
            (Analysis.resume_version_id.in_(version_ids) | Analysis.job_version_id.in_(version_ids)),
        )
    ).all() if version_ids else []
    pool_rows = db.scalars(
        select(JobPoolItem).where(
            JobPoolItem.account_id == account.id,
            JobPoolItem.deleted_at.is_(None),
            ((JobPoolItem.job_document_id == item.id) | JobPoolItem.job_document_version_id.in_(version_ids)),
        )
    ).all() if version_ids else db.scalars(select(JobPoolItem).where(JobPoolItem.account_id == account.id, JobPoolItem.job_document_id == item.id, JobPoolItem.deleted_at.is_(None))).all()
    analysis_ids = {row.id for row in analysis_rows}
    rewrite_rows = db.scalars(select(Rewrite).where(Rewrite.account_id == account.id, Rewrite.analysis_id.in_(analysis_ids), Rewrite.deleted_at.is_(None))).all() if analysis_ids else []
    interview_rows = db.scalars(select(Interview).where(Interview.account_id == account.id, Interview.analysis_id.in_(analysis_ids), Interview.deleted_at.is_(None))).all() if analysis_ids else []
    pool_ids = {row.id for row in pool_rows}
    variant_rows = db.scalars(
        select(ResumeVariant).where(
            ResumeVariant.account_id == account.id,
            ResumeVariant.deleted_at.is_(None),
            (ResumeVariant.source_resume_version_id.in_(version_ids) | ResumeVariant.job_pool_item_id.in_(pool_ids)),
        )
    ).all() if version_ids or pool_ids else []
    variant_ids = {row.id for row in variant_rows}
    variant_version_rows = db.scalars(select(ResumeVariantVersion).where(ResumeVariantVersion.account_id == account.id, ResumeVariantVersion.resume_variant_id.in_(variant_ids))).all() if variant_ids else []
    export_rows = db.scalars(select(Export).where(Export.account_id == account.id, Export.resume_variant_version_id.in_({row.id for row in variant_version_rows}))).all() if variant_version_rows else []
    fact_rows = db.scalars(select(Fact).where(Fact.account_id == account.id, Fact.document_id == item.id, Fact.deleted_at.is_(None))).all()
    fact_version_rows = db.scalars(select(FactVersion).where(FactVersion.account_id == account.id, FactVersion.fact_id.in_({row.id for row in fact_rows}), FactVersion.deleted_at.is_(None))).all() if fact_rows else []
    resource_ids = {item.public_id, *(row.public_id for row in version_rows), *(row.public_id for row in analysis_rows), *(row.public_id for row in rewrite_rows), *(row.public_id for row in interview_rows), *(row.public_id for row in pool_rows), *(row.public_id for row in variant_rows), *(row.public_id for row in variant_version_rows), *(row.public_id for row in export_rows), *(row.public_id for row in fact_version_rows)}
    task_ids = {row.task_id for row in [*analysis_rows, *rewrite_rows, *interview_rows, *export_rows] if row.task_id}
    task_ids.update(row.id for row in db.scalars(select(Task).where(Task.account_id == account.id, Task.status.in_(["queued", "running", "retry_wait"]))).all() if _task_references(row, resource_ids))
    version_count = len(version_rows)
    analysis_count = len(analysis_rows)
    running_count = db.scalar(select(func.count(Task.id)).where(Task.account_id == account.id, Task.status.in_(["queued", "running"]))) or 0
    _require_deletion_match(request, _impact_version(item.public_id, item.updated_at.isoformat(), version_count, analysis_count, running_count))
    storage_keys = {value for value in [item.file_path, *(row.file_path for row in export_rows)] if value}
    files = _mark_files_deleted(
        db,
        account.id,
        storage_keys=storage_keys,
        purposes={"document_source", "resume_pdf"},
    )
    for value in storage_keys:
        path = private_path(value, settings.data_dir)
        if path not in files:
            files.append(path)
    now = now_utc()
    _mark_tasks_cancelled(db, account.id, resource_ids=resource_ids, task_ids=task_ids, reason="来源资料已删除")
    item.deleted_at = now
    item.status = "deleted"
    db.query(DocumentVersion).filter(DocumentVersion.document_id == item.id, DocumentVersion.deleted_at.is_(None)).update({DocumentVersion.deleted_at: now}, synchronize_session=False)
    db.query(DocumentDraft).filter(DocumentDraft.document_id == item.id, DocumentDraft.status != "confirmed").update({DocumentDraft.status: "expired"})
    db.query(Analysis).filter(Analysis.id.in_(analysis_ids)).update({Analysis.deleted_at: now, Analysis.result: None}, synchronize_session=False) if analysis_ids else None
    db.query(Rewrite).filter(Rewrite.id.in_({row.id for row in rewrite_rows})).update({Rewrite.deleted_at: now}, synchronize_session=False) if rewrite_rows else None
    db.query(Interview).filter(Interview.id.in_({row.id for row in interview_rows})).update({Interview.deleted_at: now, Interview.questions: None, Interview.answers: None, Interview.summary: None}, synchronize_session=False) if interview_rows else None
    db.query(ResumeVariant).filter(ResumeVariant.id.in_(variant_ids)).update({ResumeVariant.deleted_at: now, ResumeVariant.status: "deleted"}, synchronize_session=False) if variant_ids else None
    db.query(Export).filter(Export.id.in_({row.id for row in export_rows})).update({Export.status: "expired", Export.file_path: None, Export.failure_code: "SOURCE_DELETED"}, synchronize_session=False) if export_rows else None
    db.query(Fact).filter(Fact.id.in_({row.id for row in fact_rows})).update({Fact.deleted_at: now, Fact.status: "deleted"}, synchronize_session=False) if fact_rows else None
    db.query(FactVersion).filter(FactVersion.id.in_({row.id for row in fact_version_rows})).update({FactVersion.deleted_at: now}, synchronize_session=False) if fact_version_rows else None
    if pool_rows:
        db.query(JobPoolItem).filter(JobPoolItem.id.in_(pool_ids)).update({JobPoolItem.deleted_at: now, JobPoolItem.analysis_status: "deleted", JobPoolItem.source_url: None, JobPoolItem.job_fields: {}}, synchronize_session=False)
    db.query(Preference).filter(Preference.account_id == account.id, Preference.subject_document_id == item.id, Preference.deleted_at.is_(None)).update({Preference.deleted_at: now, Preference.status: "archived", Preference.is_default: False}, synchronize_session=False)
    db.commit()
    _unlink_after_commit(files)
    return _no_content(request)


@router.get("/job-pool/items/{item_id}/deletion-impact", tags=["job-pool"])
def pool_deletion_impact(request: Request, account: WebAccount, item_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    item = _pool(db, account.id, item_id)
    analysis_count = db.scalar(select(func.count(Analysis.id)).where(Analysis.job_pool_item_id == item.id, Analysis.deleted_at.is_(None))) or 0
    version = _impact_version(item.public_id, item.revision, analysis_count)
    return _ok(request, {"resource_id": item.public_id, "resource_type": "job_pool_item", "affected": {"analyses": analysis_count, "apply_entry": db.scalar(select(func.count(ApplyClick.id)).where(ApplyClick.job_pool_item_id == item.id)) or 0}, "impact_version": version}, headers={"ETag": f'"{version}"'})


@router.delete("/job-pool/items/{item_id}", tags=["job-pool"])
def delete_pool_item(request: Request, account: WebAccount, item_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    item = _pool(db, account.id, item_id)
    analysis_rows = db.scalars(select(Analysis).where(Analysis.job_pool_item_id == item.id, Analysis.deleted_at.is_(None))).all()
    rewrite_rows = db.scalars(select(Rewrite).where(Rewrite.account_id == account.id, Rewrite.analysis_id.in_({row.id for row in analysis_rows}), Rewrite.deleted_at.is_(None))).all() if analysis_rows else []
    interview_rows = db.scalars(select(Interview).where(Interview.account_id == account.id, Interview.analysis_id.in_({row.id for row in analysis_rows}), Interview.deleted_at.is_(None))).all() if analysis_rows else []
    variant_rows = db.scalars(select(ResumeVariant).where(ResumeVariant.account_id == account.id, ResumeVariant.job_pool_item_id == item.id, ResumeVariant.deleted_at.is_(None))).all()
    variant_version_rows = db.scalars(select(ResumeVariantVersion).where(ResumeVariantVersion.account_id == account.id, ResumeVariantVersion.resume_variant_id.in_({row.id for row in variant_rows}))).all() if variant_rows else []
    export_rows = db.scalars(select(Export).where(Export.account_id == account.id, Export.resume_variant_version_id.in_({row.id for row in variant_version_rows}))).all() if variant_version_rows else []
    resource_ids = {item.public_id, *(row.public_id for row in analysis_rows), *(row.public_id for row in rewrite_rows), *(row.public_id for row in interview_rows), *(row.public_id for row in variant_rows), *(row.public_id for row in variant_version_rows), *(row.public_id for row in export_rows)}
    task_ids = {row.task_id for row in [*analysis_rows, *rewrite_rows, *interview_rows, *export_rows] if row.task_id}
    task_ids.update(row.id for row in db.scalars(select(Task).where(Task.account_id == account.id, Task.status.in_(["queued", "running", "retry_wait"]))).all() if _task_references(row, resource_ids))
    analysis_count = len(analysis_rows)
    _require_deletion_match(request, _impact_version(item.public_id, item.revision, analysis_count))
    storage_keys = {row.file_path for row in export_rows if row.file_path}
    files = _mark_files_deleted(db, account.id, storage_keys=storage_keys, purposes={"resume_pdf"})
    for value in storage_keys:
        path = private_path(value, settings.data_dir)
        if path not in files:
            files.append(path)
    now = now_utc()
    _mark_tasks_cancelled(db, account.id, resource_ids=resource_ids, task_ids=task_ids, reason="匹配池岗位已删除")
    item.deleted_at = now
    item.analysis_status = "deleted"
    item.source_url = None
    item.job_fields = {}
    db.query(Analysis).filter(Analysis.id.in_({row.id for row in analysis_rows})).update({Analysis.deleted_at: now, Analysis.result: None}, synchronize_session=False) if analysis_rows else None
    db.query(Rewrite).filter(Rewrite.id.in_({row.id for row in rewrite_rows})).update({Rewrite.deleted_at: now}, synchronize_session=False) if rewrite_rows else None
    db.query(Interview).filter(Interview.id.in_({row.id for row in interview_rows})).update({Interview.deleted_at: now, Interview.questions: None, Interview.answers: None, Interview.summary: None}, synchronize_session=False) if interview_rows else None
    db.query(ResumeVariant).filter(ResumeVariant.id.in_({row.id for row in variant_rows})).update({ResumeVariant.deleted_at: now, ResumeVariant.status: "deleted"}, synchronize_session=False) if variant_rows else None
    db.query(Export).filter(Export.id.in_({row.id for row in export_rows})).update({Export.status: "expired", Export.file_path: None, Export.failure_code: "SOURCE_DELETED"}, synchronize_session=False) if export_rows else None
    db.commit()
    _unlink_after_commit(files)
    return _no_content(request)


@router.get("/analyses/{analysis_id}/deletion-impact", tags=["analyses"])
def analysis_deletion_impact(request: Request, account: WebAccount, analysis_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    item = _analysis(db, account.id, analysis_id)
    version = _impact_version(item.public_id, item.updated_at.isoformat())
    return _ok(request, {"resource_id": item.public_id, "resource_type": "analysis", "affected": {"rewrites": db.scalar(select(func.count(Rewrite.id)).where(Rewrite.analysis_id == item.id, Rewrite.deleted_at.is_(None))) or 0, "interviews": db.scalar(select(func.count(Interview.id)).where(Interview.analysis_id == item.id, Interview.deleted_at.is_(None))) or 0}, "impact_version": version}, headers={"ETag": f'"{version}"'})


@router.delete("/analyses/{analysis_id}", tags=["analyses"])
def delete_analysis(request: Request, account: WebAccount, analysis_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    item = _analysis(db, account.id, analysis_id)
    rewrite_rows = db.scalars(select(Rewrite).where(Rewrite.analysis_id == item.id, Rewrite.deleted_at.is_(None))).all()
    interview_rows = db.scalars(select(Interview).where(Interview.analysis_id == item.id, Interview.deleted_at.is_(None))).all()
    task_ids = {value for value in [item.task_id, *(row.task_id for row in rewrite_rows), *(row.task_id for row in interview_rows)] if value}
    resource_ids = {item.public_id, *(row.public_id for row in rewrite_rows), *(row.public_id for row in interview_rows)}
    task_ids.update(row.id for row in db.scalars(select(Task).where(Task.account_id == account.id, Task.status.in_(["queued", "running", "retry_wait"]))).all() if _task_references(row, resource_ids))
    _require_deletion_match(request, _impact_version(item.public_id, item.updated_at.isoformat()))
    now = now_utc()
    _mark_tasks_cancelled(db, account.id, resource_ids=resource_ids, task_ids=task_ids, reason="分析报告已删除")
    item.deleted_at = now
    item.result = None
    db.query(Rewrite).filter(Rewrite.id.in_({row.id for row in rewrite_rows})).update({Rewrite.deleted_at: now}, synchronize_session=False) if rewrite_rows else None
    db.query(Interview).filter(Interview.id.in_({row.id for row in interview_rows})).update({Interview.deleted_at: now, Interview.questions: None, Interview.answers: None, Interview.summary: None}, synchronize_session=False) if interview_rows else None
    db.commit()
    return _no_content(request)


@router.get("/interviews/{interview_id}/deletion-impact", tags=["interviews"])
def interview_deletion_impact(request: Request, account: WebAccount, interview_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    item = _interview(db, account.id, interview_id)
    version = _impact_version(item.public_id, item.revision)
    return _ok(request, {"resource_id": item.public_id, "resource_type": "interview", "affected": {"questions": db.scalar(select(func.count(InterviewQuestion.id)).where(InterviewQuestion.interview_id == item.id)) or 0, "answers": db.scalar(select(func.count(InterviewAnswer.id)).where(InterviewAnswer.interview_id == item.id)) or 0}, "impact_version": version}, headers={"ETag": f'"{version}"'})


@router.delete("/interviews/{interview_id}", tags=["interviews"])
def delete_interview(request: Request, account: WebAccount, interview_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    item = _interview(db, account.id, interview_id)
    _require_deletion_match(request, _impact_version(item.public_id, item.revision))
    now = now_utc()
    task_ids = {item.task_id} if item.task_id else set()
    task_ids.update(
        row.id
        for row in db.scalars(
            select(Task).where(Task.account_id == account.id, Task.status.in_(["queued", "running", "retry_wait"]))
        ).all()
        if _task_references(row, {item.public_id})
    )
    _mark_tasks_cancelled(db, account.id, resource_ids={item.public_id}, task_ids=task_ids, reason="面试会话已删除")
    item.deleted_at = now
    item.questions = None
    item.answers = None
    item.summary = None
    db.commit()
    return _no_content(request)


# ------------------------------------ 期望与事实 ------------------------------------


def _preference(db: Session, account_id: int, public_id: str) -> Preference:
    item = db.scalar(
        select(Preference).where(
            Preference.public_id == public_id,
            Preference.account_id == account_id,
            Preference.deleted_at.is_(None),
        )
    )
    if item is None:
        raise NotFoundError("岗位期望不存在")
    return item


def _latest_preference_version(db: Session, preference_id: int) -> PreferenceVersion | None:
    return db.scalar(
        select(PreferenceVersion)
        .where(PreferenceVersion.preference_id == preference_id, PreferenceVersion.deleted_at.is_(None))
        .order_by(PreferenceVersion.version_no.desc())
    )


def _preference_view(db: Session, item: Preference, *, include_versions: bool = False) -> dict[str, Any]:
    current = _latest_preference_version(db, item.id)
    data: dict[str, Any] = {
        "id": item.public_id,
        "display_name": item.display_name,
        "context": "candidate" if item.subject_document_id else "self",
        "subject_document_id": db.get(Document, item.subject_document_id).public_id if item.subject_document_id and db.get(Document, item.subject_document_id) else None,
        "is_default": item.is_default,
        "status": item.status,
        "revision": item.revision,
        "version": (
            {
                "id": current.public_id,
                "version_no": current.version_no,
                "content": current.content,
                "source_type": current.source_type,
                "confirmed_at": current.confirmed_at.isoformat(),
            }
            if current
            else None
        ),
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if include_versions:
        versions = db.scalars(
            select(PreferenceVersion)
            .where(PreferenceVersion.preference_id == item.id, PreferenceVersion.deleted_at.is_(None))
            .order_by(PreferenceVersion.version_no.desc())
        ).all()
        data["versions"] = [
            {"id": row.public_id, "version_no": row.version_no, "content": row.content, "confirmed_at": row.confirmed_at.isoformat()}
            for row in versions
        ]
    return data


def _validate_preference(account: Account, payload: PreferenceRequest, db: Session) -> int | None:
    content = payload.preference
    if not isinstance(content, dict):
        raise DomainError("PREFERENCE_INVALID", "岗位期望必须是对象", 422)
    if payload.context == "candidate":
        require_recruiter(account)
        if not payload.subject_document_id:
            raise DomainError("PREFERENCE_SUBJECT_REQUIRED", "招聘方期望必须指向候选人简历", 422)
        subject = _document(db, account.id, payload.subject_document_id)
        if subject.subject_type != "candidate_resume":
            raise DomainError("PREFERENCE_SUBJECT_INVALID", "期望只能关联候选人简历", 422)
        return subject.id
    require_seeker(account)
    if payload.subject_document_id:
        raise DomainError("PREFERENCE_SUBJECT_INVALID", "求职方期望不需要候选人资料", 422)
    return None


def _validate_preference_content(content: dict[str, Any]) -> dict[str, Any]:
    """校验一条完整条件组合，保持未知、无限制和面议的语义分离。"""

    if not isinstance(content, dict):
        raise DomainError("PREFERENCE_INCOMPLETE", "岗位期望必须是对象", 422)
    result = dict(content)
    definitions = {
        # unknown 表示用户尚未提供足够信息；unrestricted 表示用户明确不限制。
        # 两者不能混为一谈，否则匹配报告会把“未知”误判成“无限制”。
        "job_title": ("specified", "unknown", "unrestricted"),
        "locations": ("specified", "unknown", "unrestricted"),
        "work_mode": ("specified", "unknown", "unrestricted"),
        "salary": ("specified", "unknown", "unrestricted", "negotiable"),
    }
    for field_name, allowed_statuses in definitions.items():
        value = result.get(field_name)
        if value is None:
            value = {"status": "unknown"}
            result[field_name] = value
        if not isinstance(value, dict) or value.get("status") not in allowed_statuses:
            raise DomainError("PREFERENCE_INCOMPLETE", f"{field_name} 的状态不合法", 422)
        status_value = value["status"]
        strength = value.get("strength", "prefer")
        if strength not in {"prefer", "important", "required", "negotiable"}:
            raise DomainError("PREFERENCE_INCOMPLETE", f"{field_name} 的条件强度不合法", 422)
        value["strength"] = strength
        if status_value != "specified":
            # 面议只允许保留原始语义，不携带一个看似可比较的数值。
            if field_name == "salary" and status_value == "negotiable":
                value["min"] = None
                value["max"] = None
            elif field_name == "locations":
                value["values"] = []
            elif field_name == "job_title":
                value["value"] = None
            elif field_name == "work_mode":
                value["value"] = None
            continue
        if field_name == "job_title":
            title = normalize_text(str(value.get("value", "")))
            if not title or len(title) > 160:
                raise DomainError("PREFERENCE_INCOMPLETE", "期望岗位不能为空", 422)
            value["value"] = title
        elif field_name == "locations":
            locations = value.get("values")
            if not isinstance(locations, list) or not 1 <= len(locations) <= 10:
                raise DomainError("PREFERENCE_INCOMPLETE", "期望地点需要填写 1 到 10 项", 422)
            cleaned = [normalize_text(str(item)) for item in locations]
            if any(not item or len(item) > 80 for item in cleaned) or len(set(cleaned)) != len(cleaned):
                raise DomainError("PREFERENCE_INCOMPLETE", "期望地点不能为空且不能重复", 422)
            value["values"] = cleaned
        elif field_name == "work_mode":
            if value.get("value") not in {"onsite", "hybrid", "remote"}:
                raise DomainError("PREFERENCE_INCOMPLETE", "办公方式不合法", 422)
        elif field_name == "salary":
            try:
                minimum = float(value.get("min"))
                maximum = float(value.get("max"))
            except (TypeError, ValueError):
                raise DomainError("PREFERENCE_INCOMPLETE", "薪资区间需要可读取的数字", 422) from None
            if minimum < 0 or maximum < minimum:
                raise DomainError("PREFERENCE_INCOMPLETE", "薪资区间无效，请检查上下限", 422)
            if value.get("currency") not in {"CNY", "USD", "EUR", "HKD", "JPY"}:
                raise DomainError("PREFERENCE_INCOMPLETE", "薪资币种不合法", 422)
            if value.get("period") not in {"monthly", "yearly"}:
                raise DomainError("PREFERENCE_INCOMPLETE", "薪资周期不合法", 422)
            if value.get("tax_basis") not in {"pre_tax", "post_tax", "gross", "net"}:
                raise DomainError("PREFERENCE_INCOMPLETE", "薪资税制口径不合法", 422)
            if value.get("salary_months") is not None and float(value["salary_months"]) <= 0:
                raise DomainError("PREFERENCE_INCOMPLETE", "发薪月数必须为正数", 422)
            value["min"] = f"{minimum:.2f}"
            value["max"] = f"{maximum:.2f}"
    return result


def _set_default_preference(db: Session, account_id: int, selected_id: int) -> None:
    db.query(Preference).filter(Preference.account_id == account_id, Preference.id != selected_id, Preference.deleted_at.is_(None)).update({Preference.is_default: False})


@router.get("/preferences", tags=["preferences"])
def list_preferences(
    request: Request,
    account: WebAccount,
    subject_document_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    statement = select(Preference).where(Preference.account_id == account.id, Preference.deleted_at.is_(None), Preference.status == "active")
    if account.registration_role == "recruiter":
        if not subject_document_id:
            raise DomainError("PREFERENCE_SUBJECT_REQUIRED", "招聘方查询期望时必须提供候选人资料", 422)
        subject = _document(db, account.id, subject_document_id)
        statement = statement.where(Preference.subject_document_id == subject.id)
    elif subject_document_id:
        raise DomainError("PREFERENCE_SUBJECT_INVALID", "求职方不能按候选人资料查询期望", 422)
    rows, page = page_rows(db, statement, Preference, cursor=cursor, limit=limit, timestamp_field="updated_at")
    return _ok(request, {"items": [_preference_view(db, item) for item in rows], "page": page})


@router.post("/preferences", tags=["preferences"], status_code=201)
def create_preference(payload: PreferenceRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    key = _idempotency_key(request)
    subject_id = _validate_preference(account, payload, db)
    content = _validate_preference_content(payload.preference)
    request_digest = payload_hash({**payload.model_dump(mode="json"), "preference": content, "subject_id": subject_id})
    if key:
        existing = db.scalar(select(Preference).where(Preference.account_id == account.id, Preference.idempotency_key == key, Preference.deleted_at.is_(None)))
        if existing is not None:
            if existing.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的岗位期望不同", 409)
            return _ok(request, _preference_view(db, existing, include_versions=True))
    item = Preference(account_id=account.id, subject_document_id=subject_id, display_name=payload.display_name.strip(), content=content, is_default=payload.is_default, status="active", idempotency_key=key, request_hash=request_digest)
    db.add(item)
    db.flush()
    db.add(PreferenceVersion(account_id=account.id, preference_id=item.id, version_no=1, content=content, source_type="candidate_confirmed" if payload.context == "candidate" else "self_confirmed", idempotency_key=key, request_hash=request_digest))
    if payload.is_default:
        _set_default_preference(db, account.id, item.id)
    db.commit()
    db.refresh(item)
    return _ok(request, _preference_view(db, item, include_versions=True), code=201)


@router.get("/preferences/{preference_id}", tags=["preferences"])
def get_preference(request: Request, account: WebAccount, preference_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    return _ok(request, _preference_view(db, _preference(db, account.id, preference_id), include_versions=True))


@router.put("/preferences/{preference_id}", tags=["preferences"])
def update_preference(payload: PreferenceUpdateRequest, request: Request, account: WebAccount, preference_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    key = _idempotency_key(request)
    item = _preference(db, account.id, preference_id)
    if payload.base_revision != item.revision:
        raise DomainError("PREFERENCE_REVISION_CONFLICT", "岗位期望已发生变化，请刷新后重试", 409, "refresh")
    subject_id = _validate_preference(account, payload, db)
    content = _validate_preference_content(payload.preference)
    request_digest = payload_hash({"preference_id": preference_id, **payload.model_dump(mode="json"), "preference": content, "subject_id": subject_id})
    if key:
        existing_version = db.scalar(select(PreferenceVersion).where(PreferenceVersion.preference_id == item.id, PreferenceVersion.idempotency_key == key, PreferenceVersion.deleted_at.is_(None)))
        if existing_version is not None:
            if existing_version.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的岗位期望版本不同", 409)
            return _ok(request, _preference_view(db, item, include_versions=True))
    item.subject_document_id = subject_id
    item.display_name = payload.display_name.strip()
    item.content = content
    item.status = payload.status
    item.revision += 1
    current = db.scalar(select(func.max(PreferenceVersion.version_no)).where(PreferenceVersion.preference_id == item.id)) or 0
    db.add(PreferenceVersion(account_id=account.id, preference_id=item.id, version_no=int(current) + 1, content=content, source_type="candidate_confirmed" if payload.context == "candidate" else "self_confirmed", idempotency_key=key, request_hash=request_digest))
    if payload.is_default:
        item.is_default = True
        _set_default_preference(db, account.id, item.id)
    else:
        item.is_default = False
    db.commit()
    return _ok(request, _preference_view(db, item, include_versions=True))


@router.delete("/preferences/{preference_id}", tags=["preferences"])
def delete_preference(request: Request, account: WebAccount, preference_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    _idempotency_key(request, required=False)
    item = _preference(db, account.id, preference_id)
    item.deleted_at = now_utc()
    item.status = "archived"
    item.is_default = False
    db.commit()
    return _ok(request, {"deleted": True, "id": preference_id})


def _fact(db: Session, account_id: int, public_id: str) -> Fact:
    item = db.scalar(select(Fact).where(Fact.public_id == public_id, Fact.account_id == account_id, Fact.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("事实不存在")
    return item


def _fact_view(db: Session, item: Fact, include_versions: bool = False) -> dict[str, Any]:
    document = db.get(Document, item.document_id)
    current = db.scalar(select(FactVersion).where(FactVersion.fact_id == item.id, FactVersion.deleted_at.is_(None)).order_by(FactVersion.version_no.desc()))
    data: dict[str, Any] = {
        "id": item.public_id,
        "document_id": document.public_id if document else None,
        "fact_category": item.fact_category,
        "fact_text": item.fact_text,
        "source_segment_key": item.source_segment_key,
        "source_type": item.source_type,
        "revision": item.revision,
        "version": {"id": current.public_id, "version_no": current.version_no, "fact_text": current.fact_text} if current else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if include_versions:
        rows = db.scalars(select(FactVersion).where(FactVersion.fact_id == item.id, FactVersion.deleted_at.is_(None)).order_by(FactVersion.version_no.desc())).all()
        data["versions"] = [{"id": row.public_id, "version_no": row.version_no, "fact_category": row.fact_category, "fact_text": row.fact_text, "confirmed_at": row.confirmed_at.isoformat()} for row in rows]
    return data


def _source_version_id(db: Session, account_id: int, public_id: str | None) -> int | None:
    if not public_id:
        return None
    return _version(db, account_id, public_id).id


def _validate_fact_source(document: Document, source_version: DocumentVersion | None, segment_key: str | None) -> None:
    """事实来源必须来自同一份仍可见的简历版本和真实段落。"""

    if document.document_type != "resume" or document.subject_type not in {"self_resume", "candidate_resume"}:
        raise DomainError("FACT_SOURCE_INVALID", "事实只能关联简历资料", 422)
    if source_version is not None:
        if source_version.document_id != document.id:
            raise DomainError("FACT_SOURCE_INVALID", "事实来源版本不属于指定简历", 422)
        if segment_key:
            segments = {str(row.get("segment_key")) for row in _resume_segments(source_version.content)}
            if segment_key not in segments:
                raise DomainError("FACT_SOURCE_INVALID", "事实来源段落不存在", 422)


@router.get("/facts", tags=["facts"])
def list_facts(
    request: Request,
    account: WebAccount,
    document_id: str | None = Query(default=None),
    fact_status: str | None = Query(default=None, alias="status", max_length=20),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_seeker(account)
    statement = select(Fact).where(Fact.account_id == account.id, Fact.deleted_at.is_(None))
    if document_id:
        statement = statement.where(Fact.document_id == _document(db, account.id, document_id).id)
    if fact_status:
        statement = statement.where(Fact.status == fact_status)
    rows, page = page_rows(db, statement, Fact, cursor=cursor, limit=limit, timestamp_field="updated_at")
    return _ok(request, {"items": [_fact_view(db, item) for item in rows], "page": page})


@router.post("/facts", tags=["facts"], status_code=201)
def create_fact(payload: FactRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    document = _document(db, account.id, payload.document_id)
    source_version_id = _source_version_id(db, account.id, payload.source_document_version_id)
    source_version = db.get(DocumentVersion, source_version_id) if source_version_id else None
    _validate_fact_source(document, source_version, payload.source_segment_key)
    request_digest = payload_hash({**payload.model_dump(mode="json"), "document_id": document.public_id})
    if key:
        existing = db.scalar(select(Fact).where(Fact.account_id == account.id, Fact.idempotency_key == key, Fact.deleted_at.is_(None)))
        if existing is not None:
            if existing.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的事实不同", 409)
            return _ok(request, _fact_view(db, existing, include_versions=True))
    fact_text = normalize_text(payload.fact_text)
    if not fact_text:
        raise DomainError("FACT_SOURCE_INVALID", "事实正文不能为空", 422)
    item = Fact(account_id=account.id, document_id=document.id, fact_category=payload.fact_category.strip(), fact_text=fact_text, source_segment_key=payload.source_segment_key, source_type=payload.source_type, idempotency_key=key, request_hash=request_digest)
    db.add(item)
    db.flush()
    db.add(FactVersion(account_id=account.id, fact_id=item.id, version_no=1, fact_category=item.fact_category, fact_text=item.fact_text, source_document_version_id=source_version_id, source_segment_key=item.source_segment_key, source_type=item.source_type, idempotency_key=key, request_hash=request_digest))
    db.commit()
    return _ok(request, _fact_view(db, item, include_versions=True), code=201)


@router.get("/facts/{fact_id}", tags=["facts"])
def get_fact(request: Request, account: WebAccount, fact_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    return _ok(request, _fact_view(db, _fact(db, account.id, fact_id), include_versions=True))


@router.post("/facts/{fact_id}/versions", tags=["facts"], status_code=201)
def create_fact_version(payload: FactVersionRequest, request: Request, account: WebAccount, fact_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    item = _fact(db, account.id, fact_id)
    if payload.base_revision != item.revision:
        raise DomainError("FACT_REVISION_CONFLICT", "事实已发生变化，请刷新后重试", 409, "refresh")
    source_version_id = _source_version_id(db, account.id, payload.source_document_version_id)
    document = _document(db, account.id, payload.document_id)
    source_version = db.get(DocumentVersion, source_version_id) if source_version_id else None
    _validate_fact_source(document, source_version, payload.source_segment_key)
    if document.id != item.document_id:
        raise DomainError("FACT_SOURCE_INVALID", "事实不能改挂到另一份资料", 422)
    request_digest = payload_hash({"fact_id": fact_id, **payload.model_dump(mode="json")})
    if key:
        existing_version = db.scalar(select(FactVersion).where(FactVersion.fact_id == item.id, FactVersion.idempotency_key == key, FactVersion.deleted_at.is_(None)))
        if existing_version is not None:
            if existing_version.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的事实版本不同", 409)
            return _ok(request, _fact_view(db, item, include_versions=True))
    fact_text = normalize_text(payload.fact_text)
    if not fact_text:
        raise DomainError("FACT_SOURCE_INVALID", "事实正文不能为空", 422)
    item.fact_category = payload.fact_category.strip()
    item.fact_text = fact_text
    item.source_segment_key = payload.source_segment_key
    item.source_type = payload.source_type
    item.revision += 1
    current = db.scalar(select(func.max(FactVersion.version_no)).where(FactVersion.fact_id == item.id)) or 0
    db.add(FactVersion(account_id=account.id, fact_id=item.id, version_no=int(current) + 1, fact_category=item.fact_category, fact_text=item.fact_text, source_document_version_id=source_version_id, source_segment_key=item.source_segment_key, source_type=item.source_type, idempotency_key=key, request_hash=request_digest))
    db.commit()
    return _ok(request, _fact_view(db, item, include_versions=True), code=201)


@router.get("/facts/{fact_id}/deletion-impact", tags=["facts"])
def fact_deletion_impact(request: Request, account: WebAccount, fact_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    item = _fact(db, account.id, fact_id)
    version_count = db.scalar(select(func.count(FactVersion.id)).where(FactVersion.fact_id == item.id, FactVersion.deleted_at.is_(None))) or 0
    version = _impact_version(item.public_id, item.revision, version_count)
    return _ok(request, {"resource_id": item.public_id, "resource_type": "fact", "affected": {"versions": version_count}, "impact_version": version}, headers={"ETag": f'"{version}"'})


@router.delete("/facts/{fact_id}", tags=["facts"])
def delete_fact(request: Request, account: WebAccount, fact_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    require_seeker(account)
    item = _fact(db, account.id, fact_id)
    version_rows = db.scalars(select(FactVersion).where(FactVersion.fact_id == item.id, FactVersion.deleted_at.is_(None))).all()
    version_count = len(version_rows)
    _require_deletion_match(request, _impact_version(item.public_id, item.revision, version_count))
    resource_ids = {item.public_id, *(row.public_id for row in version_rows)}
    task_ids = {
        row.id
        for row in db.scalars(select(Task).where(Task.account_id == account.id, Task.status.in_(["queued", "running", "retry_wait"]))).all()
        if _task_references(row, resource_ids)
    }
    now = now_utc()
    _mark_tasks_cancelled(db, account.id, resource_ids=resource_ids, task_ids=task_ids, reason="补充事实已删除")
    item.deleted_at = now
    item.status = "deleted"
    db.query(FactVersion).filter(FactVersion.fact_id == item.id, FactVersion.deleted_at.is_(None)).update({FactVersion.deleted_at: now}, synchronize_session=False)
    db.commit()
    return _no_content(request)


# ------------------------------------ 改写与岗位版 ------------------------------------


def _analysis(db: Session, account_id: int, public_id: str) -> Analysis:
    item = db.scalar(select(Analysis).where(Analysis.public_id == public_id, Analysis.account_id == account_id, Analysis.deleted_at.is_(None)))
    if item is None or item.status not in {"succeeded", "available"}:
        raise NotFoundError("分析报告不存在或尚未完成")
    return item


def _rewrite(db: Session, account_id: int, public_id: str) -> Rewrite:
    item = db.scalar(select(Rewrite).where(Rewrite.public_id == public_id, Rewrite.account_id == account_id, Rewrite.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("改写任务不存在")
    return item


def _resume_segments(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [segment for section in content.get("sections", []) for segment in section.get("segments", []) if isinstance(segment, dict) and segment.get("text")]


def _normalise_resume_variant_content(content: dict[str, Any]) -> dict[str, Any]:
    """把已确认简历转换成岗位版内容 Schema，并拒绝富文本和跨模块字段。"""

    if not isinstance(content, dict):
        raise DomainError("RESUME_CONTENT_INVALID", "岗位版简历内容必须是对象", 422)
    schema_version = content.get("schema_version", "document-content-v1")
    if schema_version not in {"document-content-v1", "resume-variant-content-v1"}:
        raise DomainError("RESUME_CONTENT_INVALID", "岗位版简历内容 Schema 版本不受支持", 422)
    sections = content.get("sections")
    if not isinstance(sections, list) or not sections:
        raise DomainError("RESUME_CONTENT_INVALID", "岗位版简历至少需要一个资料模块", 422)
    section_keys: set[str] = set()
    segment_keys: set[str] = set()
    normalized_sections: list[dict[str, Any]] = []
    for position, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise DomainError("RESUME_CONTENT_INVALID", "岗位版模块结构不合法", 422)
        section_key = str(section.get("section_key", "")).strip()
        if not section_key or len(section_key) > 96 or section_key in section_keys:
            raise DomainError("RESUME_CONTENT_INVALID", "岗位版模块标识必须唯一且不超过 96 个字符", 422)
        source_position = section.get("position", position)
        try:
            if int(source_position) != position:
                raise ValueError
        except (TypeError, ValueError):
            raise DomainError("RESUME_CONTENT_INVALID", "岗位版模块 position 必须从 1 连续递增", 422) from None
        raw_segments = section.get("segments", [])
        if not isinstance(raw_segments, list):
            raise DomainError("RESUME_CONTENT_INVALID", "岗位版 segments 必须是数组", 422)
        normalized_segments: list[dict[str, Any]] = []
        for segment in raw_segments:
            if not isinstance(segment, dict):
                raise DomainError("RESUME_CONTENT_INVALID", "岗位版段落结构不合法", 422)
            segment_key = str(segment.get("segment_key", "")).strip()
            text_value = normalize_text(str(segment.get("text", "")))
            if not segment_key or len(segment_key) > 96 or segment_key in segment_keys:
                raise DomainError("RESUME_CONTENT_INVALID", "岗位版 segment_key 必须唯一且不超过 96 个字符", 422)
            if not text_value or len(text_value) > MAX_TEXT_CHARS:
                raise DomainError("RESUME_CONTENT_INVALID", "岗位版段落正文不能为空且不能超过长度限制", 422)
            # PDF 会再次转义；这里同时拒绝 HTML 标签，避免把客户端富文本当成内容协议。
            if re.search(r"<\/?[A-Za-z][^>]*>", text_value):
                raise DomainError("RESUME_CONTENT_INVALID", "岗位版内容不接受 HTML 标签", 422)
            segment_keys.add(segment_key)
            normalized_segments.append(
                {
                    "segment_key": segment_key,
                    "text": text_value,
                    "source": str(segment.get("source") or "user_confirmed"),
                    **({"source_line": int(segment["source_line"])} if segment.get("source_line") is not None else {}),
                }
            )
        section_keys.add(section_key)
        normalized_sections.append(
            {
                "section_key": section_key,
                "section_type": str(section.get("section_type") or section_key),
                "title": normalize_text(str(section.get("title") or section_key)),
                "position": position,
                "segments": normalized_segments,
            }
        )
    if not segment_keys:
        raise DomainError("RESUME_CONTENT_INVALID", "岗位版简历不能没有段落正文", 422)
    if content.get("job_fields") not in (None, {}):
        raise DomainError("RESUME_CONTENT_INVALID", "岗位版简历不能携带岗位字段", 422)
    return {"schema_version": "resume-variant-content-v1", "sections": normalized_sections}


def _validate_resume_layout(content: dict[str, Any], layout: dict[str, Any] | None) -> dict[str, Any]:
    """校验并规范岗位版排版白名单，保证页面预览和 PDF 使用同一组值。"""

    if not isinstance(layout, dict):
        raise DomainError("RESUME_LAYOUT_INVALID", "岗位版排版必须是对象", 422)
    allowed_keys = {
        "schema_version",
        "section_order",
        "font_family",
        "font_size_pt",
        "line_height",
        "section_spacing_pt",
        "bold_segment_keys",
        # 仅为读取第一版已保存记录保留别名；新响应永远返回规范键。
        "font_size",
        "line_spacing",
    }
    unexpected = set(layout) - allowed_keys
    if unexpected:
        raise DomainError("RESUME_LAYOUT_INVALID", "排版包含不支持的字段", 422)
    if layout.get("schema_version", "resume-layout-v1") != "resume-layout-v1":
        raise DomainError("RESUME_LAYOUT_INVALID", "排版 Schema 版本不受支持", 422)
    font_family = layout.get("font_family", "noto_sans_sc")
    if font_family != "noto_sans_sc":
        raise DomainError("RESUME_LAYOUT_INVALID", "首版只支持 noto_sans_sc 字体", 422)

    def number(name: str, fallback_name: str, default: float, minimum: float, maximum: float) -> str:
        value = layout.get(name, layout.get(fallback_name, default))
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise DomainError("RESUME_LAYOUT_INVALID", f"{name} 必须是数字", 422) from None
        if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
            raise DomainError("RESUME_LAYOUT_INVALID", f"{name} 超出允许范围", 422)
        return f"{parsed:.2f}"

    section_keys = [str(section["section_key"]) for section in content["sections"]]
    order = layout.get("section_order", section_keys)
    if not isinstance(order, list) or [str(item) for item in order] != list(order):
        raise DomainError("RESUME_LAYOUT_INVALID", "section_order 必须是字符串数组", 422)
    order = [str(item) for item in order]
    if len(order) != len(set(order)) or set(order) != set(section_keys):
        raise DomainError("RESUME_LAYOUT_INVALID", "section_order 必须完整且不能重复", 422)
    bold_keys = layout.get("bold_segment_keys", [])
    if not isinstance(bold_keys, list):
        raise DomainError("RESUME_LAYOUT_INVALID", "bold_segment_keys 必须是数组", 422)
    all_segment_keys = {str(item["segment_key"]) for item in _resume_segments(content)}
    bold_keys = [str(item) for item in bold_keys]
    if len(bold_keys) != len(set(bold_keys)) or not set(bold_keys).issubset(all_segment_keys):
        raise DomainError("RESUME_LAYOUT_INVALID", "加粗段落必须来自当前岗位版内容", 422)
    return {
        "schema_version": "resume-layout-v1",
        "section_order": order,
        "font_family": font_family,
        "font_size_pt": number("font_size_pt", "font_size", 10.5, 9.0, 12.0),
        "line_height": number("line_height", "line_spacing", 1.4, 1.2, 1.8),
        "section_spacing_pt": number("section_spacing_pt", "section_spacing", 8.0, 4.0, 16.0),
        "bold_segment_keys": bold_keys,
    }


def _rewrite_for_variant(db: Session, account: Account, pool: JobPoolItem, resume_version: DocumentVersion, rewrite_id: str | None) -> Rewrite | None:
    if not rewrite_id:
        return None
    rewrite = _rewrite(db, account.id, rewrite_id)
    analysis = db.get(Analysis, rewrite.analysis_id)
    if analysis is None or analysis.account_id != account.id or analysis.job_pool_item_id != pool.id or rewrite.resume_version_id != resume_version.id:
        raise DomainError("RESUME_REWRITE_INVALID", "改写结果不是当前岗位和简历版本的结果", 409)
    if rewrite.status != "available":
        raise DomainError("RESUME_REWRITE_INVALID", "改写结果尚未完成，不能制作岗位版简历", 409, "wait")
    return rewrite


def _apply_rewrite_to_content(db: Session, content: dict[str, Any], rewrite: Rewrite | None) -> dict[str, Any]:
    if rewrite is None:
        return content
    segments = db.scalars(select(RewriteSegment).where(RewriteSegment.rewrite_id == rewrite.id)).all()
    by_key = {row.segment_key: row for row in segments}
    decisions = db.scalars(select(RewriteDecision).where(RewriteDecision.rewrite_id == rewrite.id).order_by(RewriteDecision.decision_no, RewriteDecision.id)).all()
    latest: dict[str, RewriteDecision] = {}
    for decision in decisions:
        latest[decision.segment_key] = decision
    result = {**content, "sections": []}
    for section in content["sections"]:
        section_copy = {**section, "segments": []}
        for segment in section.get("segments", []):
            segment_copy = dict(segment)
            rewrite_segment = by_key.get(str(segment.get("segment_key")))
            decision = latest.get(str(segment.get("segment_key")))
            if rewrite_segment and decision and decision.decision == "adopt":
                segment_copy["text"] = normalize_text(decision.edited_text or rewrite_segment.suggested_text or segment_copy["text"])
            section_copy["segments"].append(segment_copy)
        result["sections"].append(section_copy)
    return result


def _rewrite_view(db: Session, item: Rewrite) -> dict[str, Any]:
    segments = db.scalars(select(RewriteSegment).where(RewriteSegment.rewrite_id == item.id).order_by(RewriteSegment.id)).all()
    return {
        "id": item.public_id,
        "status": item.status,
        "analysis_id": db.get(Analysis, item.analysis_id).public_id if db.get(Analysis, item.analysis_id) else None,
        "source_resume_version_id": db.get(DocumentVersion, item.resume_version_id).public_id if db.get(DocumentVersion, item.resume_version_id) else None,
        "task": task_view(db.get(Task, item.task_id)) if item.task_id and db.get(Task, item.task_id) else None,
        "segments": [
            {
                "id": row.public_id,
                "source_segment_key": row.segment_key,
                "original_text": row.original_text,
                "suggested_text": row.suggested_text,
                "rationale": row.rationale,
                "current_decision": row.current_decision,
                "decision_no": row.current_decision_no,
                "evidence": [
                    {"source_type": evidence.source_type, "source_id": evidence.source_id, "quote": evidence.quote}
                    for evidence in db.scalars(select(RewriteEvidence).where(RewriteEvidence.rewrite_segment_id == row.id)).all()
                ],
            }
            for row in segments
        ],
    }


@router.post("/rewrites", tags=["rewrites"])
def create_rewrite(payload: RewriteRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    if not payload.confirm_usage:
        raise DomainError("USAGE_CONFIRMATION_REQUIRED", "开始改写前需要确认消耗 1 次改写", 422)
    analysis = _analysis(db, account.id, payload.analysis_id)
    resume_version = _version(db, account.id, payload.source_resume_version_id)
    if resume_version.id != analysis.resume_version_id:
        raise DomainError("REWRITE_INPUT_INVALID", "改写简历版本不是报告使用的冻结版本", 409)
    job_version = db.get(DocumentVersion, analysis.job_version_id)
    if job_version is None:
        raise NotFoundError("岗位版本不存在")
    all_segments = {str(row.get("segment_key")): row for row in _resume_segments(resume_version.content)}
    selected = [all_segments[key_name] for key_name in payload.segment_keys if key_name in all_segments]
    if len(selected) != len(set(payload.segment_keys)):
        raise DomainError("REWRITE_SEGMENT_INVALID", "所选简历段落不存在", 422)
    facts: list[dict[str, Any]] = []
    for fact_public_id in payload.fact_version_ids:
        fact_version = db.scalar(select(FactVersion).where(FactVersion.public_id == fact_public_id, FactVersion.account_id == account.id, FactVersion.deleted_at.is_(None)))
        if fact_version is None:
            raise NotFoundError("事实版本不存在")
        facts.append({"fact_text": fact_version.fact_text, "source_type": fact_version.source_type, "id": fact_version.public_id})
    input_data = {"analysis_id": analysis.public_id, "resume_version_id": resume_version.public_id, "segment_keys": payload.segment_keys, "fact_version_ids": payload.fact_version_ids}
    task, reservation, existed = create_task(db, account, "rewrite", input_data, feature="rewrite", idempotency_key=key, input_refs=[("analysis", analysis.public_id, None, payload_hash(analysis.result)), ("resume_version", resume_version.public_id, None, payload_hash(resume_version.content))])
    if existed:
        item = db.scalar(select(Rewrite).where(Rewrite.task_id == task.id, Rewrite.account_id == account.id, Rewrite.deleted_at.is_(None)))
        return _ok(request, {"task": task_view(task), "rewrite": _rewrite_view(db, item) if item else None}, code=202)
    item = Rewrite(account_id=account.id, analysis_id=analysis.id, resume_version_id=resume_version.id, status="queued", task_id=task.id)
    db.add(item)
    db.flush()
    db.commit()

    def work() -> Any:
        # 保留 ModelResult 的 token 与 provider 元数据，由任务服务统一记成本和预算。
        return get_model_provider().rewrite(selected, (job_version.content.get("job_fields") or {}), facts)

    def save_result(value: dict[str, Any]) -> None:
        item.status = "available"
        item.segments = value.get("segments", [])
        for segment in value.get("segments", []):
            row = RewriteSegment(account_id=account.id, rewrite_id=item.id, segment_key=str(segment.get("source_segment_key")), original_text=str(segment.get("original_text", "")), suggested_text=segment.get("suggested_text"), rationale=segment.get("rationale"), status="available")
            db.add(row)
            db.flush()
            for evidence in segment.get("evidence", []):
                source_id = str(evidence.get("source_id") or evidence.get("fact_text") or "resume")
                db.add(RewriteEvidence(account_id=account.id, rewrite_segment_id=row.id, source_type=str(evidence.get("source_type") or "fact"), source_id=source_id, quote=evidence.get("fact_text") or evidence.get("quote")))

    try:
        run_local_task(db, task, reservation, work, on_success=save_result, feature="rewrite")
    except Exception as exc:
        LOGGER.exception("rewrite task failed")
        db.rollback()
        try:
            fail_task(db, task.id, reservation.id if reservation else None, exc)
            db.commit()
        except Exception:
            db.rollback()
        raise DomainError("REWRITE_FAILED", "改写任务失败，请重试", 503, "retry")
    db.refresh(item)
    return _ok(request, {"task": task_view(task), "rewrite": _rewrite_view(db, item)}, code=202)


@router.get("/rewrites/{rewrite_id}", tags=["rewrites"])
def get_rewrite(request: Request, account: WebAccount, rewrite_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    return _ok(request, _rewrite_view(db, _rewrite(db, account.id, rewrite_id)))


@router.get("/rewrites/{rewrite_id}/deletion-impact", tags=["rewrites"])
def rewrite_deletion_impact(request: Request, account: WebAccount, rewrite_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    item = _rewrite(db, account.id, rewrite_id)
    segment_count = db.scalar(select(func.count(RewriteSegment.id)).where(RewriteSegment.rewrite_id == item.id)) or 0
    version = _impact_version(item.public_id, item.updated_at.isoformat(), segment_count)
    return _ok(request, {"resource_id": item.public_id, "resource_type": "rewrite", "affected": {"segments": segment_count}, "impact_version": version}, headers={"ETag": f'"{version}"'})


@router.delete("/rewrites/{rewrite_id}", tags=["rewrites"])
def delete_rewrite(request: Request, account: WebAccount, rewrite_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    require_seeker(account)
    item = _rewrite(db, account.id, rewrite_id)
    segment_count = db.scalar(select(func.count(RewriteSegment.id)).where(RewriteSegment.rewrite_id == item.id)) or 0
    _require_deletion_match(request, _impact_version(item.public_id, item.updated_at.isoformat(), segment_count))
    resource_ids = {item.public_id}
    task_ids = {item.task_id} if item.task_id else set()
    task_ids.update(
        row.id
        for row in db.scalars(select(Task).where(Task.account_id == account.id, Task.status.in_(["queued", "running", "retry_wait"]))).all()
        if _task_references(row, resource_ids)
    )
    now = now_utc()
    _mark_tasks_cancelled(db, account.id, resource_ids=resource_ids, task_ids=task_ids, reason="改写结果已删除")
    item.deleted_at = now
    item.status = "deleted"
    item.segments = None
    db.query(RewriteEvidence).filter(RewriteEvidence.rewrite_segment_id.in_(select(RewriteSegment.id).where(RewriteSegment.rewrite_id == item.id))).delete(synchronize_session=False)
    db.query(RewriteSegment).filter(RewriteSegment.rewrite_id == item.id).delete(synchronize_session=False)
    db.query(RewriteDecision).filter(RewriteDecision.rewrite_id == item.id).delete(synchronize_session=False)
    db.query(ResumeVariantVersion).filter(ResumeVariantVersion.rewrite_id == item.id).update({ResumeVariantVersion.rewrite_id: None}, synchronize_session=False)
    db.commit()
    return _no_content(request)


@router.post("/rewrites/{rewrite_id}/segments/{segment_id}/decisions", tags=["rewrites"])
def decide_rewrite(payload: DecisionRequest, request: Request, account: WebAccount, rewrite_id: str = PathParam(min_length=1, max_length=36), segment_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    item = _rewrite(db, account.id, rewrite_id)
    segment = db.scalar(select(RewriteSegment).where(RewriteSegment.public_id == segment_id, RewriteSegment.rewrite_id == item.id, RewriteSegment.account_id == account.id))
    if segment is None:
        raise NotFoundError("改写段落不存在")
    normalized_decision = {"adopted": "adopt", "edited": "adopt", "rejected": "keep_original"}.get(payload.decision, payload.decision)
    request_digest = payload_hash({"rewrite_id": rewrite_id, "segment_id": segment_id, **payload.model_dump(mode="json"), "decision": normalized_decision})
    if key:
        existing_decision = db.scalar(select(RewriteDecision).where(RewriteDecision.account_id == account.id, RewriteDecision.idempotency_key == key))
        if existing_decision is not None:
            if existing_decision.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的改写决定不同", 409)
            return _ok(request, _rewrite_view(db, item))
    if normalized_decision == "adopt" and payload.decision == "edited" and not (payload.edited_text or "").strip():
        raise DomainError("REWRITE_EDIT_REQUIRED", "编辑采用时必须提供正文", 422)
    if normalized_decision != "adopt" and payload.edited_text is not None:
        raise DomainError("REWRITE_DECISION_INVALID", "保留原文或撤回时不能提交编辑正文", 422)
    if payload.base_decision_no != segment.current_decision_no:
        raise DomainError("REWRITE_SEGMENT_CONFLICT", "改写决定已发生变化，请刷新后重试", 409, "refresh")
    segment.current_decision = normalized_decision
    segment.current_decision_no += 1
    db.add(RewriteDecision(account_id=account.id, rewrite_id=item.id, segment_key=segment.segment_key, decision=normalized_decision, edited_text=(payload.edited_text or "").strip() or None, decision_no=segment.current_decision_no, idempotency_key=key, request_hash=request_digest))
    db.commit()
    return _ok(request, {"rewrite": _rewrite_view(db, item), "decision": {"decision": normalized_decision, "decision_no": segment.current_decision_no}})


@router.post("/resumes", tags=["resumes"], status_code=201)
def create_resume_variant(payload: ResumeRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    pool = _pool(db, account.id, payload.job_pool_item_id)
    resume_version = _version(db, account.id, payload.source_resume_version_id)
    source_document = db.get(Document, resume_version.document_id)
    if source_document is None or source_document.document_type != "resume":
        raise DomainError("RESUME_SOURCE_INVALID", "岗位版起点必须是已确认的简历版本", 422)
    rewrite = _rewrite_for_variant(db, account, pool, resume_version, payload.rewrite_id)
    content = _apply_rewrite_to_content(db, _normalise_resume_variant_content(resume_version.content), rewrite)
    content = _normalise_resume_variant_content(content)
    layout = _validate_resume_layout(content, {})
    request_digest = payload_hash({"job_pool_item_id": pool.public_id, "source_resume_version_id": resume_version.public_id, "title": payload.title.strip(), "rewrite_id": payload.rewrite_id, "content": content, "layout": layout})
    existing = db.scalar(select(ResumeVariant).where(ResumeVariant.account_id == account.id, ResumeVariant.idempotency_key == key, ResumeVariant.deleted_at.is_(None))) if key else None
    if existing is not None:
        if existing.request_hash != request_digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的岗位版简历不同", 409)
        return _ok(request, _variant_view(db, existing))
    variant = ResumeVariant(account_id=account.id, job_pool_item_id=pool.id, source_resume_version_id=resume_version.id, title=payload.title.strip(), status="editing", idempotency_key=key, request_hash=request_digest)
    db.add(variant)
    db.flush()
    db.add(ResumeVariantVersion(account_id=account.id, resume_variant_id=variant.id, version_no=1, content=content, layout=layout, rewrite_id=rewrite.id if rewrite else None, idempotency_key=key, request_hash=request_digest, template_version="resume-template-v1"))
    db.commit()
    return _ok(request, _variant_view(db, variant), code=201)


def _pool(db: Session, account_id: int, public_id: str) -> JobPoolItem:
    item = db.scalar(select(JobPoolItem).where(JobPoolItem.public_id == public_id, JobPoolItem.account_id == account_id, JobPoolItem.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("匹配池岗位不存在")
    return item


def _pool_by_pk(db: Session, account_id: int, pool_id: int) -> JobPoolItem:
    """按内部主键读取岗位，同时重复执行账号和软删除隔离校验。

    岗位版简历内部保存的是 ``job_pool_item_id`` 主键，而对外接口使用岗位的
    ``public_id``。这两个标识不能混用，否则 PostgreSQL 会在 varchar 与 integer
    比较时直接报错，且可能绕过岗位资源隔离。
    """

    item = db.scalar(select(JobPoolItem).where(JobPoolItem.id == pool_id, JobPoolItem.account_id == account_id, JobPoolItem.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("匹配池岗位不存在")
    return item


def _variant(db: Session, account_id: int, public_id: str) -> ResumeVariant:
    item = db.scalar(select(ResumeVariant).where(ResumeVariant.public_id == public_id, ResumeVariant.account_id == account_id, ResumeVariant.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("岗位版简历不存在")
    return item


def _variant_view(db: Session, item: ResumeVariant) -> dict[str, Any]:
    versions = db.scalars(select(ResumeVariantVersion).where(ResumeVariantVersion.resume_variant_id == item.id).order_by(ResumeVariantVersion.version_no.desc())).all()
    pool = db.get(JobPoolItem, item.job_pool_item_id)
    return {"id": item.public_id, "title": item.title, "status": item.status, "revision": item.revision, "job_pool_item_id": pool.public_id if pool else None, "versions": [{"id": row.public_id, "version_no": row.version_no, "content": row.content, "layout": row.layout, "rewrite_id": db.get(Rewrite, row.rewrite_id).public_id if row.rewrite_id and db.get(Rewrite, row.rewrite_id) else None, "template_version": row.template_version, "created_at": row.created_at.isoformat()} for row in versions], "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}


@router.get("/resumes", tags=["resumes"])
def list_resume_variants(
    request: Request,
    account: WebAccount,
    job_pool_item_id: str | None = Query(default=None, max_length=36),
    variant_status: str | None = Query(default=None, alias="status", max_length=24),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_seeker(account)
    statement = select(ResumeVariant).where(ResumeVariant.account_id == account.id, ResumeVariant.deleted_at.is_(None))
    if job_pool_item_id:
        statement = statement.where(ResumeVariant.job_pool_item_id == _pool(db, account.id, job_pool_item_id).id)
    if variant_status:
        statement = statement.where(ResumeVariant.status == variant_status)
    rows, page = page_rows(db, statement, ResumeVariant, cursor=cursor, limit=limit, timestamp_field="updated_at")
    return _ok(request, {"items": [_variant_view(db, item) for item in rows], "page": page})


@router.get("/resumes/{resume_id}", tags=["resumes"])
def get_resume_variant(request: Request, account: WebAccount, resume_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    return _ok(request, _variant_view(db, _variant(db, account.id, resume_id)))


@router.get("/resumes/{resume_id}/deletion-impact", tags=["resumes"])
def resume_variant_deletion_impact(request: Request, account: WebAccount, resume_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    item = _variant(db, account.id, resume_id)
    version_count = db.scalar(select(func.count(ResumeVariantVersion.id)).where(ResumeVariantVersion.resume_variant_id == item.id)) or 0
    export_count = db.scalar(select(func.count(Export.id)).where(Export.resume_variant_version_id.in_(select(ResumeVariantVersion.id).where(ResumeVariantVersion.resume_variant_id == item.id)))) or 0
    version = _impact_version(item.public_id, item.revision, version_count, export_count)
    return _ok(request, {"resource_id": item.public_id, "resource_type": "resume_variant", "affected": {"versions": version_count, "exports": export_count}, "impact_version": version}, headers={"ETag": f'"{version}"'})


@router.delete("/resumes/{resume_id}", tags=["resumes"])
def delete_resume_variant(request: Request, account: WebAccount, resume_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    require_seeker(account)
    item = _variant(db, account.id, resume_id)
    version_rows = db.scalars(select(ResumeVariantVersion).where(ResumeVariantVersion.resume_variant_id == item.id)).all()
    export_rows = db.scalars(select(Export).where(Export.account_id == account.id, Export.resume_variant_version_id.in_({row.id for row in version_rows}))).all() if version_rows else []
    version_count = len(version_rows)
    export_count = len(export_rows)
    _require_deletion_match(request, _impact_version(item.public_id, item.revision, version_count, export_count))
    resource_ids = {item.public_id, *(row.public_id for row in version_rows), *(row.public_id for row in export_rows)}
    task_ids = {row.task_id for row in export_rows if row.task_id}
    task_ids.update(
        row.id
        for row in db.scalars(select(Task).where(Task.account_id == account.id, Task.status.in_(["queued", "running", "retry_wait"]))).all()
        if _task_references(row, resource_ids)
    )
    storage_keys = {row.file_path for row in export_rows if row.file_path}
    files = _mark_files_deleted(db, account.id, storage_keys=storage_keys, purposes={"resume_pdf"})
    for value in storage_keys:
        path = private_path(value, settings.data_dir)
        if path not in files:
            files.append(path)
    now = now_utc()
    _mark_tasks_cancelled(db, account.id, resource_ids=resource_ids, task_ids=task_ids, reason="岗位版简历已删除")
    item.deleted_at = now
    item.status = "deleted"
    db.query(Export).filter(Export.id.in_({row.id for row in export_rows})).update({Export.status: "expired", Export.file_path: None, Export.failure_code: "SOURCE_DELETED"}, synchronize_session=False) if export_rows else None
    db.commit()
    _unlink_after_commit(files)
    return _no_content(request)


@router.post("/resumes/{resume_id}/versions", tags=["resumes"], status_code=201)
def create_resume_variant_version(payload: ResumeVersionRequest, request: Request, account: WebAccount, resume_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    item = _variant(db, account.id, resume_id)
    content = _normalise_resume_variant_content(payload.content)
    layout = _validate_resume_layout(content, payload.layout)
    request_digest = payload_hash({"resume_id": resume_id, "base_revision": payload.base_revision, "content": content, "layout": layout, "template_version": payload.template_version, "rewrite_id": payload.rewrite_id})
    existing = db.scalar(select(ResumeVariantVersion).where(ResumeVariantVersion.account_id == account.id, ResumeVariantVersion.resume_variant_id == item.id, ResumeVariantVersion.idempotency_key == key)) if key else None
    if existing is not None:
        if existing.request_hash != request_digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的岗位版版本不同", 409)
        return _ok(request, _variant_view(db, item))
    if payload.base_revision != item.revision:
        raise DomainError("RESUME_REVISION_CONFLICT", "岗位版简历已变化，请刷新后重试", 409, "refresh")
    source_version = db.get(DocumentVersion, item.source_resume_version_id)
    if source_version is None or source_version.account_id != account.id:
        raise DomainError("RESUME_SOURCE_INVALID", "岗位版起点资料已不可用", 409)
    pool = _pool_by_pk(db, account.id, item.job_pool_item_id)
    rewrite = _rewrite_for_variant(db, account, pool, source_version, payload.rewrite_id) if payload.rewrite_id else None
    current = db.scalar(select(func.max(ResumeVariantVersion.version_no)).where(ResumeVariantVersion.resume_variant_id == item.id)) or 0
    item.revision += 1
    row = ResumeVariantVersion(account_id=account.id, resume_variant_id=item.id, version_no=int(current) + 1, content=content, layout=layout, rewrite_id=rewrite.id if rewrite else None, idempotency_key=key, request_hash=request_digest, template_version=payload.template_version)
    db.add(row)
    db.commit()
    return _ok(request, _variant_view(db, item), code=201)


@router.post("/exports", tags=["exports"])
def create_resume_export(payload: ExportRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    version = db.scalar(select(ResumeVariantVersion).where(ResumeVariantVersion.public_id == payload.resume_variant_version_id, ResumeVariantVersion.account_id == account.id))
    if version is None:
        raise NotFoundError("岗位版简历版本不存在")
    existing_task = db.scalar(select(Task).where(Task.account_id == account.id, Task.task_type == "resume_export", Task.idempotency_key == key, Task.deleted_at.is_(None)))
    if existing_task:
        export = db.scalar(select(Export).where(Export.task_id == existing_task.id, Export.account_id == account.id))
        return _ok(request, {"task": task_view(existing_task), "export": _export_view(export) if export else None}, code=202)
    task, _, _ = create_task(db, account, "resume_export", {"resume_variant_version_id": version.public_id}, idempotency_key=key, input_refs=[("resume_variant_version", version.public_id, version.version_no, payload_hash(version.content))])
    export = Export(account_id=account.id, resume_variant_version_id=version.id, status="queued", task_id=task.id)
    db.add(export)
    db.flush()
    db.commit()
    output_dir = settings.export_dir.resolve()
    output_path = output_dir / f"{export.public_id}.pdf"
    render_path = output_dir / f".{export.public_id}.pdf.rendering"
    published_path: Path | None = None
    attempt: TaskAttempt | None = None
    try:
        attempt = mark_task_running(db, task)
        db.commit()
        render_path.unlink(missing_ok=True)
        render_resume_pdf(version.content, version.layout, render_path, "岗位版简历")
        byte_size = render_path.stat().st_size
        ensure_storage_capacity(db, byte_size)
        render_path.replace(output_path)
        published_path = output_path
        export.file_path = storage_key(output_path, settings.data_dir)
        export.content_hash = sha256_bytes(output_path.read_bytes())
        export.status = "available"
        export.completed_at = now_utc()
        db.add(
            StoredFile(
                account_id=account.id,
                purpose="resume_pdf",
                original_filename="purslyx-resume.pdf",
                media_type="application/pdf",
                byte_size=byte_size,
                sha256=export.content_hash,
                storage_key=storage_key(output_path, settings.data_dir),
                status="available",
            )
        )
        task_result = {"export_id": export.public_id, "file_ready": True}
        finish_task(db, task, None, task_result, attempt=attempt)
        db.commit()
    except Exception as exc:
        db.rollback()
        render_path.unlink(missing_ok=True)
        if published_path is not None:
            published_path.unlink(missing_ok=True)
        fail_task(db, task.id, None, exc, attempt_id=attempt.id if attempt else None)
        export = db.get(Export, export.id)
        if export:
            export.status = "failed"
            export.failure_code = getattr(exc, "code", "PDF_EXPORT_FAILED")
        db.commit()
        if isinstance(exc, DomainError):
            raise exc
        raise DomainError("PDF_EXPORT_FAILED", "PDF 导出失败，请重试", 503, "retry") from exc
    db.refresh(export)
    return _ok(request, {"task": task_view(task), "export": _export_view(export)}, code=202)


def _export_view(item: Export | None) -> dict[str, Any] | None:
    if item is None:
        return None
    file_path = private_path(item.file_path, settings.data_dir) if item.file_path else None
    return {"id": item.public_id, "status": item.status, "file_available": bool(file_path and file_path.is_file()), "content_hash": item.content_hash, "created_at": item.created_at.isoformat(), "completed_at": item.completed_at.isoformat() if item.completed_at else None}


@router.get("/exports/{export_id}", tags=["exports"])
def get_export(request: Request, account: WebAccount, export_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    item = db.scalar(select(Export).where(Export.public_id == export_id, Export.account_id == account.id))
    if item is None:
        raise NotFoundError("导出任务不存在")
    return _ok(request, _export_view(item))


@router.get("/exports/{export_id}/file", tags=["exports"])
def get_export_file(request: Request, account: WebAccount, export_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    item = db.scalar(select(Export).where(Export.public_id == export_id, Export.account_id == account.id))
    file_path = private_path(item.file_path, settings.data_dir) if item and item.file_path else None
    if item is None or item.status != "available" or file_path is None or not file_path.is_file():
        raise NotFoundError("PDF 尚未生成或已清理")
    response = FileResponse(file_path, filename="purslyx-resume.pdf", media_type="application/pdf")
    response.headers["Cache-Control"] = "private, no-store"
    return response


# ------------------------------------ 浏览器岗位与匹配池 ------------------------------------


class BrowserDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["boss", "liepin"]
    source_url: str = Field(min_length=1, max_length=1024)
    capture_schema_version: str = Field(default="browser-job-capture-v1", max_length=80)
    captured_at: datetime
    job_title: str | None = Field(default=None, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    location_text: str | None = Field(default=None, max_length=300)
    work_mode: Literal["onsite", "hybrid", "remote"] | None = None
    salary_text: str | None = Field(default=None, max_length=300)
    job_description_text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    missing_field_codes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("job_description_text")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        value = normalize_text(value)
        if not value:
            raise ValueError("岗位正文不能为空")
        return value


class PoolCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: dict[str, Any]
    preference_version_id: str | None = None
    analysis: dict[str, Any] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_document_version_id: str
    preference_version_id: str | None = None
    base_revision: int = Field(ge=1)
    confirm_usage: bool


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_type: Literal["recruiter_single"]
    resume_document_version_id: str
    job_document_version_id: str
    preference_version_id: str | None = None
    confirm_usage: bool


def _normalize_job_url(platform: str, value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    domains = {
        "boss": ("zhipin.com", "www.zhipin.com"),
        "liepin": ("liepin.com", "www.liepin.com"),
    }[platform]
    if parsed.scheme != "https" or not host or not any(host == domain or host.endswith("." + domain) for domain in domains):
        raise DomainError("JOB_CAPTURE_PAGE_UNSUPPORTED", "只接受受支持平台的 HTTPS 岗位详情页", 422, "paste_text")
    # 片段与追踪参数不影响岗位内容去重，但保留路径和必要查询参数。
    allowed_query = "&".join(part for part in parsed.query.split("&") if part and not part.lower().startswith(("utm_", "spm=")))
    return f"https://{host}{parsed.path.rstrip('/') or '/'}" + (f"?{allowed_query}" if allowed_query else "")


def _job_fields_from_draft(draft: BrowserJobDraft) -> dict[str, Any]:
    parsed = parse_job_text(draft.job_description_text or "")
    fields = dict(parsed.get("job_fields") or {})
    location_value = draft.location_text or fields.get("location_text")
    locations = (
        [item.strip() for item in re.split(r"[/／、,，|]", location_value) if item.strip()]
        if isinstance(location_value, str)
        else (location_value or [])
    )
    fields.update(
        {
            "title": draft.job_title or fields.get("title"),
            "company_name": draft.company_name or fields.get("company_name"),
            "location_text": location_value,
            "locations": locations,
            "work_mode": draft.work_mode or fields.get("work_mode"),
            "salary_text": draft.salary_text or fields.get("salary_text"),
            "source_url": draft.source_url,
        }
    )
    return fields


def _browser_draft_view(draft: BrowserJobDraft) -> dict[str, Any]:
    return {
        "id": draft.public_id,
        "platform": draft.platform,
        "source_url": draft.source_url,
        "job_title": draft.job_title,
        "company_name": draft.company_name,
        "location_text": draft.location_text,
        "work_mode": draft.work_mode,
        "salary_text": draft.salary_text,
        "job_description_text": draft.job_description_text,
        "missing_field_codes": draft.missing_field_codes or [],
        "status": draft.status,
        "capture_schema_version": draft.capture_schema_version,
        "expires_at": draft.expires_at.isoformat(),
        "created_at": draft.created_at.isoformat(),
        "updated_at": draft.updated_at.isoformat(),
        "confirm_path": f"/job-pool/items?browser_draft_id={draft.public_id}",
    }


@router.post("/browser/job-drafts", tags=["browser"])
def create_browser_draft(payload: BrowserDraftRequest, request: Request, account: BrowserAccount, db: Session = Depends(get_db)) -> JSONResponse:
    key = _idempotency_key(request)
    source_url = _normalize_job_url(payload.platform, payload.source_url)
    source_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    normalized_payload = payload.model_dump(mode="json")
    normalized_payload["source_url"] = source_url
    request_digest = payload_hash(normalized_payload)
    if key:
        existing_by_key = db.scalar(
            select(BrowserJobDraft).where(
                BrowserJobDraft.account_id == account.id,
                BrowserJobDraft.idempotency_key == key,
            )
        )
        if existing_by_key is not None:
            if existing_by_key.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的岗位草稿不同", 409)
            return _ok(request, _browser_draft_view(existing_by_key))
    content_hash = payload_hash(payload.model_dump(exclude={"source_url", "captured_at"}))
    existing = db.scalar(select(BrowserJobDraft).where(BrowserJobDraft.account_id == account.id, BrowserJobDraft.platform == payload.platform, BrowserJobDraft.source_url_hash == source_hash, BrowserJobDraft.content_hash == content_hash))
    if existing:
        return _ok(request, _browser_draft_view(existing))
    draft = BrowserJobDraft(
        account_id=account.id,
        platform=payload.platform,
        source_url=source_url,
        source_url_hash=source_hash,
        content_hash=content_hash,
        capture_schema_version=payload.capture_schema_version,
        captured_at=payload.captured_at,
        job_title=payload.job_title,
        company_name=payload.company_name,
        location_text=payload.location_text,
        work_mode=payload.work_mode,
        salary_text=payload.salary_text,
        job_description_text=payload.job_description_text,
        missing_field_codes=payload.missing_field_codes,
        captured_payload=normalized_payload,
        expires_at=now_utc() + timedelta(days=2),
        status="awaiting_confirmation",
        idempotency_key=key,
        request_hash=request_digest,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return _ok(request, _browser_draft_view(draft), code=201)


@router.get("/browser/job-drafts/{draft_id}", tags=["browser"])
def get_browser_draft(request: Request, account: BrowserAccount, draft_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    draft = db.scalar(select(BrowserJobDraft).where(BrowserJobDraft.public_id == draft_id, BrowserJobDraft.account_id == account.id))
    if draft is None:
        raise NotFoundError("岗位草稿不存在")
    if draft.expires_at <= now_utc() or draft.status == "expired":
        draft.status = "expired"
        db.commit()
        raise DomainError("JOB_DRAFT_EXPIRED", "岗位草稿已过期", 410)
    return _ok(request, _browser_draft_view(draft))


def _preference_version(
    db: Session,
    account_id: int,
    public_id: str | None,
    *,
    subject_document_id: int | None = None,
) -> PreferenceVersion | None:
    if not public_id:
        return None
    row = db.scalar(select(PreferenceVersion).where(PreferenceVersion.public_id == public_id, PreferenceVersion.account_id == account_id, PreferenceVersion.deleted_at.is_(None)))
    if row is None:
        raise NotFoundError("岗位期望版本不存在")
    preference = db.get(Preference, row.preference_id)
    if preference is None or preference.deleted_at is not None:
        raise NotFoundError("岗位期望不存在")
    if subject_document_id is not None and preference.subject_document_id != subject_document_id:
        raise DomainError("PREFERENCE_SUBJECT_INVALID", "岗位期望没有指向当前候选人简历", 422)
    if subject_document_id is None and preference.subject_document_id is not None:
        raise DomainError("PREFERENCE_SUBJECT_INVALID", "求职分析只能使用本人的岗位期望", 422)
    return row


def _pool_view(db: Session, item: JobPoolItem, *, detail: bool = False) -> dict[str, Any]:
    job_version = db.get(DocumentVersion, item.job_document_version_id) if item.job_document_version_id else None
    resume_version = db.get(DocumentVersion, item.resume_version_id) if item.resume_version_id else None
    analysis = db.scalar(select(Analysis).where(Analysis.job_pool_item_id == item.id, Analysis.deleted_at.is_(None)).order_by(Analysis.created_at.desc()))
    data: dict[str, Any] = {
        "id": item.public_id,
        "source_type": item.source_type,
        "platform": item.platform,
        "job_title": item.job_title,
        "company_name": item.company_name,
        "analysis_status": item.analysis_status,
        "blocking_reasons": item.blocking_reasons or [],
        "job_document_version": {"id": job_version.public_id, "version_no": job_version.version_no} if job_version else None,
        "resume_version_id": resume_version.public_id if resume_version else None,
        "preference_id": db.get(Preference, item.preference_id).public_id if item.preference_id and db.get(Preference, item.preference_id) else None,
        "latest_analysis": {"id": analysis.public_id, "status": analysis.status, "ability_score": analysis.ability_score} if analysis else None,
        "apply_action": _apply_action(item) if item.source_url else {"available": False},
        "revision": item.revision,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if detail:
        data["source_url"] = item.source_url
        data["job_content"] = job_version.content if job_version else item.job_fields
        data["analysis_history"] = [
            {"id": row.public_id, "status": row.status, "ability_score": row.ability_score, "completed_at": row.completed_at.isoformat() if row.completed_at else None}
            for row in db.scalars(select(Analysis).where(Analysis.job_pool_item_id == item.id, Analysis.deleted_at.is_(None)).order_by(Analysis.created_at.desc())).all()
        ]
    return data


def _analysis_view(db: Session, item: Analysis, *, detail: bool = True) -> dict[str, Any]:
    resume_version = db.get(DocumentVersion, item.resume_version_id)
    job_version = db.get(DocumentVersion, item.job_version_id)
    data = {
        "id": item.public_id,
        "context_type": item.context_type,
        "status": item.status,
        "task": task_view(db.get(Task, item.task_id)) if item.task_id and db.get(Task, item.task_id) else None,
        "input_versions": [
            value
            for value in [
                {"type": "resume", "id": resume_version.public_id} if resume_version else None,
                {"type": "job", "id": job_version.public_id} if job_version else None,
                (
                    {"type": "preference", "id": db.get(PreferenceVersion, item.preference_version_id).public_id}
                    if item.preference_version_id and db.get(PreferenceVersion, item.preference_version_id)
                    else None
                ),
            ]
            if value is not None
        ],
        "preference_id": db.get(Preference, item.preference_id).public_id if item.preference_id and db.get(Preference, item.preference_id) else None,
        "job_category": item.job_category,
        "ability_score": item.ability_score,
        "evidence_coverage": item.evidence_coverage,
        "scoring_rule_version": item.scoring_rule_version,
        "result_schema_version": item.result_schema_version,
        "prompt_version": item.prompt_version,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }
    if detail:
        data["report"] = item.result
    return data


def _persist_analysis_details(
    db: Session,
    account: Account,
    analysis: Analysis,
    report: dict[str, Any],
    resume_version_id: int,
    preference_content: dict[str, Any] | None = None,
    job_content: dict[str, Any] | None = None,
) -> None:
    for dimension in report.get("dimensions", []):
        dim_row = AnalysisDimensionScore(account_id=account.id, analysis_id=analysis.id, dimension_key=str(dimension.get("key")), label=str(dimension.get("label", "")), base_weight=float(dimension.get("base_weight") or 0), effective_weight=float(dimension.get("effective_weight") or 0), score=dimension.get("score"), evidence_status=str(dimension.get("evidence_status", "unknown")), summary=dimension.get("summary"))
        db.add(dim_row)
        db.flush()
        for position, requirement in enumerate(dimension.get("requirements", []), start=1):
            finding = str(requirement.get("status", "needs_confirmation"))
            coefficient = {"supported": 1.0, "partially_supported": 0.5, "gap": 0.0, "needs_confirmation": 0.0}.get(finding, 0.0)
            req_row = AnalysisRequirementResult(account_id=account.id, analysis_id=analysis.id, requirement_id=str(requirement.get("requirement_id")), dimension_key=str(dimension.get("key")), position_no=position, requirement_text=str(requirement.get("job_quote", "")), finding_type=finding, match_coefficient=coefficient, coverage_flag=finding != "needs_confirmation", explanation=str(requirement.get("explanation", "")))
            db.add(req_row)
            db.flush()
            for evidence in requirement.get("evidence", []):
                db.add(AnalysisEvidence(account_id=account.id, analysis_id=analysis.id, requirement_result_id=req_row.id, document_version_id=resume_version_id, segment_key=evidence.get("segment_key"), quote=str(evidence.get("quote", "")), source_type=str(evidence.get("source_type", "resume"))))
    preference_content = preference_content or {}
    job_fields = (job_content or {}).get("job_fields") or {}
    preference_fields = {
        "job_title": preference_content.get("job_title"),
        "location": preference_content.get("locations"),
        "work_mode": preference_content.get("work_mode"),
        "salary": preference_content.get("salary"),
    }
    job_values = {
        "job_title": {"status": "specified", "value": job_fields.get("title")} if job_fields.get("title") else {"status": "unknown"},
        "location": {"status": "specified", "values": job_fields.get("locations") or []} if job_fields.get("locations") else {"status": "unknown"},
        "work_mode": {"status": "specified", "value": job_fields.get("work_mode")} if job_fields.get("work_mode") else {"status": "unknown"},
        "salary": job_fields.get("salary") or {"status": "unknown"},
    }
    for condition in report.get("conditions", []):
        code = str(condition.get("condition", "unknown"))
        preference_value = preference_fields.get(code)
        db.add(
            AnalysisConditionResult(
                account_id=account.id,
                analysis_id=analysis.id,
                condition_code=code,
                result_status=str(condition.get("status", "unknown")),
                preference_value=preference_value,
                job_value=job_values.get(code),
                strength=(preference_value or {}).get("strength") if isinstance(preference_value, dict) else None,
                explanation=str(condition.get("explanation", "")),
            )
        )


def _start_analysis(db: Session, account: Account, *, context_type: str, resume_version: DocumentVersion, job_version: DocumentVersion, preference: PreferenceVersion | None, pool: JobPoolItem | None, key: str) -> tuple[Analysis, Task, bool]:
    resume_document = db.get(Document, resume_version.document_id)
    job_document = db.get(Document, job_version.document_id)
    if resume_document is None or resume_document.account_id != account.id or resume_document.deleted_at is not None or resume_document.document_type != "resume":
        raise DomainError("ANALYSIS_INPUT_INVALID", "简历版本类型不正确", 422)
    if job_document is None or job_document.account_id != account.id or job_document.deleted_at is not None or not _document_type_is_job(job_document.document_type):
        raise DomainError("ANALYSIS_INPUT_INVALID", "岗位版本类型不正确", 422)
    input_data = {"context_type": context_type, "resume_version_id": resume_version.public_id, "job_version_id": job_version.public_id, "preference_version_id": preference.public_id if preference else None}
    task, reservation, existed = create_task(
        db,
        account,
        "analysis",
        input_data,
        feature="analysis",
        idempotency_key=key,
        input_refs=[
            ("resume_version", resume_version.public_id, resume_version.version_no, payload_hash(resume_version.content)),
            ("job_version", job_version.public_id, job_version.version_no, payload_hash(job_version.content)),
            *(
                [("preference_version", preference.public_id, preference.version_no, payload_hash(preference.content))]
                if preference
                else []
            ),
        ],
    )
    if existed:
        analysis = db.scalar(select(Analysis).where(Analysis.task_id == task.id, Analysis.account_id == account.id, Analysis.deleted_at.is_(None)))
        if analysis is None:
            raise DomainError("ANALYSIS_IDEMPOTENCY_CONFLICT", "幂等任务缺少分析结果引用", 409)
        return analysis, task, True
    analysis = Analysis(
        account_id=account.id,
        context_type=context_type,
        status="queued",
        job_pool_item_id=pool.id if pool else None,
        resume_version_id=resume_version.id,
        job_version_id=job_version.id,
        preference_id=preference.preference_id if preference else None,
        preference_version_id=preference.id if preference else None,
        task_id=task.id,
    )
    db.add(analysis)
    db.flush()
    if pool:
        pool.resume_version_id = resume_version.id
        pool.preference_id = preference.preference_id if preference else None
        pool.analysis_status = "queued"
    db.commit()

    def work() -> Any:
        # 不在 API 层丢弃模型调用元数据，Worker／预算服务需要使用真实 token。
        return get_model_provider().analyze(resume_version.content, job_version.content, preference.content if preference else None, context_type)

    def save_result(report: dict[str, Any]) -> None:
        analysis.status = "available"
        analysis.job_category = report.get("job_category", "general")
        analysis.ability_score = report.get("ability_score")
        analysis.evidence_coverage = report.get("evidence_coverage")
        analysis.result = report
        analysis.scoring_rule_version = report.get("scoring_rule_version", "ability-v0.1")
        analysis.result_schema_version = report.get("result_schema_version", "analysis-result-v1")
        analysis.prompt_version = report.get("prompt_version", "analysis-local-v1")
        analysis.completed_at = now_utc()
        _persist_analysis_details(db, account, analysis, report, resume_version.id, preference.content if preference else None, job_version.content)
        if pool:
            pool.analysis_status = "available"
            pool.revision += 1

    try:
        from .services import run_local_task

        run_local_task(db, task, reservation, work, on_success=save_result, feature="analysis")
    except Exception as exc:
        # 本地执行失败时，任务服务已经把次数释放并把任务置为 failed；业务结果也必须
        # 同步落为失败，否则岗位和报告会永久停在 queued，前端无法给出重试入口。
        db.refresh(analysis)
        analysis.status = "failed"
        if pool is not None:
            db.refresh(pool)
            pool.analysis_status = "failed"
            pool.blocking_reasons = ["分析失败，可重试"]
        db.commit()
        raise DomainError("ANALYSIS_FAILED", "分析任务失败，请稍后重试", 503, "retry") from exc
    db.refresh(analysis)
    return analysis, task, False


@router.post("/job-pool/items", tags=["job-pool"])
def create_pool_item(payload: PoolCreateRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    source = payload.source
    source_type = source.get("type")
    preference = _preference_version(db, account.id, payload.preference_version_id)
    job_version: DocumentVersion | None = None
    browser_draft: BrowserJobDraft | None = None
    if source_type == "browser_draft":
        draft_id = str(source.get("browser_draft_id", ""))
        browser_draft = db.scalar(select(BrowserJobDraft).where(BrowserJobDraft.public_id == draft_id, BrowserJobDraft.account_id == account.id, BrowserJobDraft.status == "awaiting_confirmation"))
        if browser_draft is None:
            raise NotFoundError("浏览器岗位草稿不存在或已确认")
        if browser_draft.expires_at <= now_utc():
            browser_draft.status = "expired"
            db.commit()
            raise DomainError("JOB_DRAFT_EXPIRED", "岗位草稿已过期", 410)
        fields = _job_fields_from_draft(browser_draft)
        document = Document(account_id=account.id, document_type="job_description", subject_type="job_description", title=browser_draft.job_title or "浏览器岗位", source_type="text", status="confirmed", raw_text=browser_draft.job_description_text, draft_content={"schema_version": "document-content-v1", "sections": [], "job_fields": fields})
        db.add(document)
        db.flush()
        db.add(DocumentDraft(account_id=account.id, document_id=document.id, content=document.draft_content, status="confirmed", confirmed_at=now_utc(), expires_at=now_utc() + timedelta(days=7)))
        db.flush()
        job_version = DocumentVersion(account_id=account.id, document_id=document.id, version_no=1, content=document.draft_content)
        db.add(job_version)
        db.flush()
        browser_draft.status = "confirmed"
    elif source_type == "document_version":
        job_version = _version(db, account.id, str(source.get("job_document_version_id", "")))
        document = db.get(Document, job_version.document_id)
        if document is None or not _document_type_is_job(document.document_type):
            raise DomainError("POOL_SOURCE_INVALID", "岗位池来源必须是岗位版本", 422)
        fields = job_version.content.get("job_fields") or {}
    else:
        raise DomainError("POOL_SOURCE_INVALID", "只支持 browser_draft 或 document_version", 422)
    pool = JobPoolItem(account_id=account.id, source_type="browser_capture" if browser_draft else "manual", platform=browser_draft.platform if browser_draft else None, source_url=browser_draft.source_url if browser_draft else None, source_url_hash=browser_draft.source_url_hash if browser_draft else None, job_title=fields.get("title") or "未命名岗位", company_name=fields.get("company_name"), job_fields=fields, job_document_id=job_version.document_id, job_document_version_id=job_version.id, preference_id=preference.preference_id if preference else None, analysis_status="awaiting_requirements", blocking_reasons=[])
    db.add(pool)
    db.flush()
    start_now = bool(payload.analysis.get("start_now"))
    resume_version = _version(db, account.id, str(payload.analysis.get("resume_document_version_id", ""))) if start_now else None
    if start_now:
        if preference is None or resume_version is None or not payload.analysis.get("confirm_usage"):
            pool.blocking_reasons = ["需要已确认简历版本、岗位期望版本和使用次数确认"]
            db.commit()
            return _ok(request, _pool_view(db, pool), code=201)
        # 先保留岗位，再尝试预留分析次数；次数不足时岗位仍应可见并进入待满足条件，
        # 不能因为一次计次失败把用户刚确认的岗位一并回滚。
        db.commit()
        try:
            analysis, task, _ = _start_analysis(db, account, context_type="seeker_pool", resume_version=resume_version, job_version=job_version, preference=preference, pool=pool, key=key)
        except DomainError as exc:
            if exc.code not in {"USAGE_INSUFFICIENT", "TASK_QUEUE_LIMIT_REACHED", "BUDGET_LIMIT_REACHED", "BUDGET_CONCURRENCY_LIMIT"}:
                raise
            pool = _pool(db, account.id, pool.public_id)
            pool.analysis_status = "awaiting_requirements"
            pool.blocking_reasons = [exc.message]
            db.commit()
            return _ok(request, _pool_view(db, pool), code=201)
        return _ok(request, {"job_pool_item": _pool_view(db, pool), "analysis": _analysis_view(db, analysis), "task": task_view(task)}, code=202)
    db.commit()
    return _ok(request, _pool_view(db, pool), code=201)


@router.get("/job-pool/items", tags=["job-pool"])
def list_pool_items(
    request: Request,
    account: WebAccount,
    analysis_status: str | None = Query(default=None, max_length=32),
    platform: str | None = Query(default=None, max_length=32),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_seeker(account)
    statement = select(JobPoolItem).where(JobPoolItem.account_id == account.id, JobPoolItem.deleted_at.is_(None))
    if analysis_status:
        statement = statement.where(JobPoolItem.analysis_status == analysis_status)
    if platform:
        statement = statement.where(JobPoolItem.platform == platform)
    if created_from:
        statement = statement.where(JobPoolItem.created_at >= created_from)
    if created_to:
        statement = statement.where(JobPoolItem.created_at < created_to)
    if created_from and created_to and created_from >= created_to:
        raise DomainError("POOL_RANGE_INVALID", "岗位时间范围无效", 422)
    rows, page = page_rows(db, statement, JobPoolItem, cursor=cursor, limit=limit, timestamp_field="updated_at")
    return _ok(request, {"items": [_pool_view(db, row) for row in rows], "page": page})


@router.get("/job-pool/items/{item_id}", tags=["job-pool"])
def get_pool_item(request: Request, account: WebAccount, item_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    return _ok(request, _pool_view(db, _pool(db, account.id, item_id), detail=True))


@router.post("/job-pool/items/{item_id}/analyze", tags=["job-pool"])
def analyze_pool_item(payload: AnalyzeRequest, request: Request, account: WebAccount, item_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    if not payload.confirm_usage:
        raise DomainError("USAGE_CONFIRMATION_REQUIRED", "开始分析前需要确认消耗 1 次分析", 422)
    pool = _pool(db, account.id, item_id)
    if payload.base_revision != pool.revision:
        raise DomainError("ANALYSIS_INPUT_STALE", "岗位信息已变化，请刷新后重试", 409, "refresh")
    resume_version = _version(db, account.id, payload.resume_document_version_id)
    job_version = db.get(DocumentVersion, pool.job_document_version_id) if pool.job_document_version_id else None
    if job_version is None:
        raise NotFoundError("岗位版本不存在")
    preference = _preference_version(db, account.id, payload.preference_version_id)
    analysis, task, _ = _start_analysis(db, account, context_type="seeker_pool", resume_version=resume_version, job_version=job_version, preference=preference, pool=pool, key=key)
    return _ok(request, {"job_pool_item": _pool_view(db, pool), "analysis": _analysis_view(db, analysis), "task": task_view(task)}, code=202)


@router.post("/analyses", tags=["analyses"])
def create_recruiter_analysis(payload: AnalysisRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_recruiter(account)
    key = _idempotency_key(request)
    if not payload.confirm_usage:
        raise DomainError("USAGE_CONFIRMATION_REQUIRED", "开始分析前需要确认消耗 1 次分析", 422)
    resume_version = _version(db, account.id, payload.resume_document_version_id)
    job_version = _version(db, account.id, payload.job_document_version_id)
    preference = _preference_version(db, account.id, payload.preference_version_id, subject_document_id=resume_version.document_id)
    analysis, task, _ = _start_analysis(db, account, context_type="recruiter_single", resume_version=resume_version, job_version=job_version, preference=preference, pool=None, key=key)
    return _ok(request, {"analysis": _analysis_view(db, analysis), "task": task_view(task)}, code=202)


@router.get("/analyses", tags=["analyses"])
def list_analyses(
    request: Request,
    account: WebAccount,
    context_type: str | None = Query(default=None, max_length=32),
    analysis_status: str | None = Query(default=None, alias="status", max_length=24),
    document_id: str | None = Query(default=None, max_length=36),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    statement = select(Analysis).where(Analysis.account_id == account.id, Analysis.deleted_at.is_(None))
    if context_type:
        statement = statement.where(Analysis.context_type == context_type)
    if analysis_status:
        statement = statement.where(Analysis.status == analysis_status)
    if document_id:
        document = _document(db, account.id, document_id)
        version_ids = select(DocumentVersion.id).where(DocumentVersion.document_id == document.id)
        statement = statement.where(Analysis.resume_version_id.in_(version_ids) | Analysis.job_version_id.in_(version_ids))
    if created_from:
        statement = statement.where(Analysis.created_at >= created_from)
    if created_to:
        statement = statement.where(Analysis.created_at < created_to)
    if created_from and created_to and created_from >= created_to:
        raise DomainError("ANALYSIS_RANGE_INVALID", "分析时间范围无效", 422)
    rows, page = page_rows(db, statement, Analysis, cursor=cursor, limit=limit, timestamp_field="created_at")
    return _ok(request, {"items": [_analysis_view(db, row, detail=False) for row in rows], "page": page})


@router.get("/analyses/{analysis_id}", tags=["analyses"])
def get_analysis_report(request: Request, account: WebAccount, analysis_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    return _ok(request, _analysis_view(db, _analysis(db, account.id, analysis_id)))


def _token_key() -> bytes:
    return (settings.token_secret.encode("utf-8") if settings.token_secret else _PROCESS_TOKEN_SECRET)


def _apply_action(item: JobPoolItem) -> dict[str, Any]:
    if not item.source_url or not item.source_url_hash or item.platform not in {"boss", "liepin"}:
        return {"available": False}
    expires = int((now_utc() + timedelta(minutes=10)).timestamp())
    nonce = secrets.token_urlsafe(10)
    payload = f"{item.public_id}|{item.source_url_hash}|{expires}|{nonce}"
    signature = hmac.new(_token_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"available": True, "click_token": f"{payload}.{signature}", "expires_at": datetime.fromtimestamp(expires, tz=now_utc().tzinfo).isoformat()}


def _verify_apply_token(token: str, item: JobPoolItem) -> None:
    parts = token.split(".")
    if len(parts) != 2:
        raise DomainError("APPLY_CLICK_CONFLICT", "去投递令牌无效", 409)
    payload, signature = parts
    expected = hmac.new(_token_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise DomainError("APPLY_CLICK_CONFLICT", "去投递令牌无效", 409)
    values = payload.split("|")
    if len(values) != 4 or values[0] != item.public_id or values[1] != item.source_url_hash:
        raise DomainError("APPLY_CLICK_CONFLICT", "岗位链接已变化，请刷新岗位详情", 409)
    try:
        expires = int(values[2])
    except ValueError as exc:
        raise DomainError("APPLY_CLICK_CONFLICT", "去投递令牌无效", 409) from exc
    if expires <= int(now_utc().timestamp()):
        raise DomainError("APPLY_CLICK_CONFLICT", "去投递令牌已过期，请刷新岗位详情", 409)
    _normalize_job_url(item.platform or "", item.source_url)


@router.post("/job-pool/items/{item_id}/go-to-apply", tags=["job-pool"])
async def go_to_apply(request: Request, account: WebAccount, item_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _write_guard(request, account)
    require_seeker(account)
    item = _pool(db, account.id, item_id)
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith("multipart/form-data"):
        form = await request.form()
        token = str(form.get("click_token") or "")
    else:
        try:
            body = await request.json()
        except Exception as exc:
            raise DomainError("APPLY_CLICK_CONFLICT", "缺少去投递令牌", 409) from exc
        token = str(body.get("click_token") or "")
    _verify_apply_token(token, item)
    if not item.source_url:
        raise DomainError("APPLY_URL_UNSUPPORTED", "当前岗位没有可用的原始链接", 422)
    token_hash = hash_secret(token)
    existing = db.scalar(select(ApplyClick).where(ApplyClick.account_id == account.id, ApplyClick.click_token_hash == token_hash))
    if existing is None:
        db.add(ApplyClick(account_id=account.id, job_pool_item_id=item.id, platform=item.platform or "", source_url_hash=item.source_url_hash or "", click_token_hash=token_hash, metric_date=shanghai_date()))
        db.commit()
    return RedirectResponse(url=item.source_url, status_code=303)


# ------------------------------------ 面试练习 ------------------------------------


class InterviewStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_pool_item_id: str
    analysis_id: str
    resume_document_version_id: str
    title: str = Field(default="岗位面试练习", min_length=1, max_length=200)
    confirm_usage: bool


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    answer_text: str = Field(min_length=1, max_length=8000)
    base_revision: int = Field(ge=1)

    @field_validator("answer_text")
    @classmethod
    def answer_not_blank(cls, value: str) -> str:
        value = normalize_text(value)
        if not value:
            raise ValueError("回答不能为空")
        return value


class FinishInterviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1)


def _interview(db: Session, account_id: int, public_id: str) -> Interview:
    item = db.scalar(select(Interview).where(Interview.public_id == public_id, Interview.account_id == account_id, Interview.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("面试会话不存在")
    return item


def _interview_view(db: Session, item: Interview) -> dict[str, Any]:
    questions = db.scalars(select(InterviewQuestion).where(InterviewQuestion.interview_id == item.id).order_by(InterviewQuestion.position_no)).all()
    answers = db.scalars(select(InterviewAnswer).where(InterviewAnswer.interview_id == item.id).order_by(InterviewAnswer.created_at)).all()
    feedback = db.scalars(select(InterviewFeedback).where(InterviewFeedback.interview_id == item.id).order_by(InterviewFeedback.id)).all()
    summary = db.scalar(select(InterviewSummary).where(InterviewSummary.interview_id == item.id).order_by(InterviewSummary.created_at.desc()))
    answer_map = {row.question_id: row for row in answers}
    feedback_map = {row.question_id: row for row in feedback}
    return {
        "id": item.public_id,
        "title": item.title,
        "status": item.status,
        "revision": item.revision,
        "usage_settled": item.usage_settled,
        "task": task_view(db.get(Task, item.task_id)) if item.task_id and db.get(Task, item.task_id) else None,
        "current_action": "answer" if item.status == "awaiting_answer" else ("wait" if item.status == "processing" else "view_summary"),
        "questions": [
            {
                "id": row.public_id,
                "question_type": row.question_type,
                "main_no": row.main_no,
                "parent_question_id": db.get(InterviewQuestion, row.parent_question_id).public_id if row.parent_question_id and db.get(InterviewQuestion, row.parent_question_id) else None,
                "position_no": row.position_no,
                "question_text": row.question_text,
                "basis": row.basis,
                "status": row.status,
                "answer": {"id": answer_map[row.id].public_id, "answer_text": answer_map[row.id].answer_text} if row.id in answer_map else None,
                "feedback": {"id": feedback_map[row.id].public_id, "status": feedback_map[row.id].status, "content": feedback_map[row.id].content, "needs_followup": feedback_map[row.id].needs_followup} if row.id in feedback_map else None,
            }
            for row in questions
        ],
        "summary": {"id": summary.public_id, "completion_type": summary.completion_type, "content": summary.content} if summary else None,
    }


@router.post("/interviews", tags=["interviews"])
def start_interview(payload: InterviewStartRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    if not payload.confirm_usage:
        raise DomainError("USAGE_CONFIRMATION_REQUIRED", "开始面试前需要确认消耗 1 场面试", 422)
    pool = _pool(db, account.id, payload.job_pool_item_id)
    analysis = _analysis(db, account.id, payload.analysis_id)
    resume_version = _version(db, account.id, payload.resume_document_version_id)
    if analysis.job_pool_item_id != pool.id or analysis.resume_version_id != resume_version.id:
        raise DomainError("INTERVIEW_SOURCE_UNAVAILABLE", "面试输入不是该岗位报告的冻结版本", 409)
    task, reservation, existed = create_task(db, account, "interview_opening", {"job_pool_item_id": pool.public_id, "analysis_id": analysis.public_id, "resume_version_id": resume_version.public_id}, feature="interview", idempotency_key=key, input_refs=[("analysis", analysis.public_id, None, payload_hash(analysis.result)), ("resume_version", resume_version.public_id, resume_version.version_no, payload_hash(resume_version.content))])
    if existed:
        item = db.scalar(select(Interview).where(Interview.task_id == task.id, Interview.account_id == account.id, Interview.deleted_at.is_(None)))
        return _ok(request, {"task": task_view(task), "interview": _interview_view(db, item) if item else None}, code=202)
    item = Interview(account_id=account.id, job_pool_item_id=pool.id, analysis_id=analysis.id, resume_version_id=resume_version.id, title=payload.title.strip(), status="opening", questions=[], answers=[])
    db.add(item)
    db.flush()
    item.task_id = task.id
    db.commit()

    def work() -> Any:
        return get_model_provider().opening_questions(analysis.result or {}, resume_version.content)

    def save_questions(value: dict[str, Any]) -> None:
        questions = value.get("questions", [])[:3]
        if len(questions) != 3:
            raise DomainError("INTERVIEW_OPENING_FAILED", "开场没有生成完整的 3 个主问题", 503, "retry")
        item.status = "awaiting_answer"
        item.questions = []
        for position, question in enumerate(questions, start=1):
            row = InterviewQuestion(account_id=account.id, interview_id=item.id, question_type="main", main_no=position, position_no=position, question_text=str(question.get("question_text", "")), basis=question.get("basis") or {"rule_version": "interview-question-basis-v1"}, status="awaiting_answer")
            db.add(row)
            db.flush()
            item.questions.append({"id": row.public_id, "question_type": row.question_type, "main_no": row.main_no, "question_text": row.question_text})
        item.current_question_id = item.questions[0]["id"]
        item.usage_settled = True

    try:
        run_local_task(db, task, reservation, work, on_success=save_questions, feature="interview")
    except Exception as exc:
        item.status = "opening_failed"
        db.commit()
        raise DomainError("INTERVIEW_OPENING_FAILED", "面试开场失败，请重试", 503, "retry") from exc
    db.refresh(item)
    return _ok(request, {"task": task_view(task), "interview": _interview_view(db, item)}, code=202)


@router.get("/interviews", tags=["interviews"])
def list_interviews(
    request: Request,
    account: WebAccount,
    interview_status: str | None = Query(default=None, alias="status", max_length=24),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_seeker(account)
    statement = select(Interview).where(Interview.account_id == account.id, Interview.deleted_at.is_(None))
    if interview_status:
        statement = statement.where(Interview.status == interview_status)
    rows, page = page_rows(db, statement, Interview, cursor=cursor, limit=limit, timestamp_field="updated_at")
    return _ok(request, {"items": [{"id": row.public_id, "title": row.title, "status": row.status, "revision": row.revision, "updated_at": row.updated_at.isoformat()} for row in rows], "page": page})


@router.get("/interviews/{interview_id}", tags=["interviews"])
def get_interview(request: Request, account: WebAccount, interview_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    require_seeker(account)
    return _ok(request, _interview_view(db, _interview(db, account.id, interview_id)))


def _public_question_id(db: Session, account_id: int, value: str) -> InterviewQuestion:
    row = db.scalar(select(InterviewQuestion).where(InterviewQuestion.public_id == value, InterviewQuestion.account_id == account_id))
    if row is None:
        raise NotFoundError("面试题目不存在")
    return row


def _finish_interview_summary(db: Session, account: Account, item: Interview, completion_type: str) -> None:
    questions = db.scalars(select(InterviewQuestion).where(InterviewQuestion.interview_id == item.id).order_by(InterviewQuestion.position_no)).all()
    answers = db.scalars(select(InterviewAnswer).where(InterviewAnswer.interview_id == item.id).order_by(InterviewAnswer.created_at)).all()
    question_values = [{"id": row.public_id, "main_no": row.main_no, "question_type": row.question_type, "question_text": row.question_text} for row in questions]
    answer_values = [{"question_id": db.get(InterviewQuestion, row.question_id).public_id, "answer_text": row.answer_text} for row in answers]
    value = get_model_provider().summary(question_values, answer_values, completion_type).value
    db.add(InterviewSummary(account_id=account.id, interview_id=item.id, completion_type=completion_type, content=value))
    item.summary = value
    item.status = "completed" if completion_type == "full" else "ended_early"
    item.revision += 1


@router.post("/interviews/{interview_id}/answers", tags=["interviews"])
def submit_answer(payload: AnswerRequest, request: Request, account: WebAccount, interview_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    key = _idempotency_key(request)
    item = _interview(db, account.id, interview_id)
    request_digest = payload_hash({"interview_id": interview_id, **payload.model_dump(mode="json")})
    existing_by_key = db.scalar(select(InterviewAnswer).where(InterviewAnswer.account_id == account.id, InterviewAnswer.idempotency_key == key))
    if existing_by_key is not None:
        if existing_by_key.request_hash != request_digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的面试回答不同", 409)
        return _ok(request, {"interview": _interview_view(db, item), "answer": {"id": existing_by_key.public_id, "answer_text": existing_by_key.answer_text}}, code=202)
    if item.status != "awaiting_answer":
        raise DomainError("INTERVIEW_NOT_AWAITING_ANSWER", "当前会话不在等待回答状态", 409)
    if payload.base_revision != item.revision:
        raise DomainError("INTERVIEW_ROUND_CONFLICT", "会话轮次已变化，请刷新当前题目", 409, "refresh")
    question = _public_question_id(db, account.id, payload.question_id)
    if question.interview_id != item.id or question.status != "awaiting_answer" or item.current_question_id != question.public_id:
        raise DomainError("INTERVIEW_ROUND_CONFLICT", "该题目不是当前可回答轮次", 409, "refresh")
    existing = db.scalar(select(InterviewAnswer).where(InterviewAnswer.interview_id == item.id, InterviewAnswer.question_id == question.id))
    if existing:
        raise DomainError("INTERVIEW_ANSWER_EXISTS", "该题已经提交过回答，不能用新的幂等键覆盖", 409, "refresh")
    answer = InterviewAnswer(account_id=account.id, interview_id=item.id, question_id=question.id, answer_text=payload.answer_text, idempotency_key=key, request_hash=request_digest)
    db.add(answer)
    question.status = "answered"
    item.status = "processing"
    item.revision += 1
    db.flush()
    task, _, existed = create_task(db, account, "interview_feedback", {"interview_id": item.public_id, "question_id": question.public_id}, idempotency_key=key)
    db.commit()
    if not existed:
        task_result: dict[str, Any] = {}

        def work() -> Any:
            return get_model_provider().feedback(
                {"question_text": question.question_text, "question_type": question.question_type},
                answer.answer_text,
            )

        def save_feedback(feedback_value: dict[str, Any]) -> None:
            db.add(
                InterviewFeedback(
                    account_id=account.id,
                    interview_id=item.id,
                    question_id=question.id,
                    status="available",
                    content=feedback_value.get("content"),
                    needs_followup=bool(feedback_value.get("needs_followup")),
                    completed_at=now_utc(),
                )
            )
            if feedback_value.get("needs_followup") and question.question_type == "main":
                existing_followup = db.scalar(
                    select(InterviewQuestion).where(
                        InterviewQuestion.interview_id == item.id,
                        InterviewQuestion.parent_question_id == question.id,
                    )
                )
                if existing_followup is not None:
                    item.current_question_id = existing_followup.public_id
                    item.status = "awaiting_answer"
                    followup_id = existing_followup.public_id
                else:
                    last_position = db.scalar(select(func.max(InterviewQuestion.position_no)).where(InterviewQuestion.interview_id == item.id)) or 0
                    followup = InterviewQuestion(
                        account_id=account.id,
                        interview_id=item.id,
                        question_type="followup",
                        main_no=question.main_no,
                        parent_question_id=question.id,
                        position_no=int(last_position) + 1,
                        question_text="请再补充你本人采取的具体行动和可以核对的结果。",
                        basis={"parent_question_id": question.public_id, "rule_version": "interview-followup-v1"},
                        status="awaiting_answer",
                    )
                    db.add(followup)
                    db.flush()
                    item.current_question_id = followup.public_id
                    item.status = "awaiting_answer"
                    followup_id = followup.public_id
                task_result.update({"question_id": question.public_id, "needs_followup": True, "followup_id": followup_id})
            else:
                next_main = db.scalar(
                    select(InterviewQuestion)
                    .where(
                        InterviewQuestion.interview_id == item.id,
                        InterviewQuestion.question_type == "main",
                        InterviewQuestion.status == "awaiting_answer",
                    )
                    .order_by(InterviewQuestion.main_no)
                )
                if next_main:
                    item.current_question_id = next_main.public_id
                    item.status = "awaiting_answer"
                else:
                    _finish_interview_summary(db, account, item, "full")
                task_result.update({"question_id": question.public_id, "needs_followup": False})

        try:
            run_local_task(
                db,
                task,
                None,
                work,
                on_success=save_feedback,
                cost_feature="interview_feedback",
                task_result=task_result,
            )
        except Exception as exc:
            item = _interview(db, account.id, interview_id)
            item.status = "awaiting_answer"
            db.commit()
            raise DomainError("INTERVIEW_FEEDBACK_FAILED", "本轮反馈失败，请重试", 503, "retry") from exc
    db.refresh(item)
    return _ok(request, {"interview": _interview_view(db, item), "answer": {"id": answer.public_id, "answer_text": answer.answer_text}}, code=202)


@router.post("/interviews/{interview_id}/finish", tags=["interviews"])
def finish_interview(payload: FinishInterviewRequest, request: Request, account: WebAccount, interview_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    require_seeker(account)
    _idempotency_key(request)
    item = _interview(db, account.id, interview_id)
    if item.status in {"completed", "ended_early"}:
        return _ok(request, _interview_view(db, item))
    if item.status != "awaiting_answer":
        raise DomainError("INTERVIEW_PROCESSING", "本轮回答仍在处理，完成后才能提前结束", 409, "wait")
    if payload.base_revision != item.revision:
        raise DomainError("INTERVIEW_ROUND_CONFLICT", "会话轮次已变化，请刷新后重试", 409, "refresh")
    for row in db.scalars(select(InterviewQuestion).where(InterviewQuestion.interview_id == item.id, InterviewQuestion.status == "awaiting_answer")).all():
        row.status = "skipped"
    _finish_interview_summary(db, account, item, "early")
    db.commit()
    return _ok(request, _interview_view(db, item))


# ------------------------------------ 任务、用量与反馈 ------------------------------------


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature: Literal["analysis", "rewrite", "interview"]
    count: int = Field(ge=1, le=10000)
    reason: str = Field(min_length=5, max_length=500)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback_type: Literal["issue", "suggestion", "payment_intent", "other"]
    content: str = Field(default="", max_length=5000)
    rating: int | None = Field(default=None, ge=1, le=5)
    payment_intent: Literal["willing", "depends_on_price", "unwilling", "not_answered"] | None = None
    context_type: Literal["general", "analysis", "rewrite", "interview", "export"] | None = None
    context_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="before")
    @classmethod
    def normalize_context_payload(cls, value: Any) -> Any:
        """兼容文档中的嵌套 context，同时把内部存储保持为扁平字段。"""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        context = data.pop("context", None)
        if context is not None:
            if not isinstance(context, dict):
                raise ValueError("context 必须是对象")
            context_type = context.get("type")
            context_id = context.get("resource_id")
            if "context_type" in data and data["context_type"] != context_type:
                raise ValueError("context.type 与 context_type 不能冲突")
            if "context_id" in data and data["context_id"] != context_id:
                raise ValueError("context.resource_id 与 context_id 不能冲突")
            data.setdefault("context_type", context_type)
            data.setdefault("context_id", context_id)
        if data.get("content") is None:
            data["content"] = ""
        return data

    @model_validator(mode="after")
    def validate_feedback(self) -> "FeedbackRequest":
        self.content = self.content.strip()
        if self.feedback_type in {"issue", "suggestion", "other"} and not self.content:
            raise ValueError("问题、建议和其他反馈必须填写内容")
        if self.feedback_type == "payment_intent" and self.payment_intent is None:
            raise ValueError("付费意愿反馈必须选择 payment_intent")
        if self.context_type in {None, "general"} and self.context_id:
            raise ValueError("general 反馈不能携带上下文资源")
        if self.context_type not in {None, "general"} and not self.context_id:
            raise ValueError("带上下文的反馈必须提供 context_id")
        return self


class AccountStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=1, max_length=300)
    base_revision: int | None = Field(default=None, ge=1)


class RoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    permission_keys: list[str] = Field(default_factory=list, max_length=50)
    status: Literal["active", "archived"] = "active"
    base_revision: int | None = Field(default=None, ge=1)
    reason: str = Field(default="后台角色变更", min_length=1, max_length=300)


class RoleAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_ids: list[str] = Field(max_length=20)
    base_revision: int | None = Field(default=None, ge=1)
    reason: str = Field(default="替换后台角色", min_length=1, max_length=300)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="user_retry", min_length=1, max_length=120)


def _task(db: Session, account_id: int, public_id: str) -> Task:
    item = db.scalar(select(Task).where(Task.public_id == public_id, Task.account_id == account_id, Task.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("任务不存在")
    return item


def _task_etag(task: Task) -> str:
    value = "|".join(
        [
            task.public_id,
            task.status,
            task.current_step or "",
            str(task.retry_count),
            task.updated_at.isoformat(),
        ]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


@router.get("/tasks", tags=["tasks"])
def list_tasks(
    request: Request,
    account: WebAccount,
    task_status: str | None = Query(default=None, alias="status", max_length=24),
    task_type: str | None = Query(default=None, max_length=40),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """工作台任务中心只返回当前账号的执行摘要，不暴露任务正文。"""

    statement = select(Task).where(Task.account_id == account.id, Task.deleted_at.is_(None))
    if task_status:
        statement = statement.where(Task.status == task_status)
    if task_type:
        statement = statement.where(Task.task_type == task_type)
    rows, page = page_rows(db, statement, Task, cursor=cursor, limit=limit, timestamp_field="created_at")
    return _ok(
        request,
        {"items": [task_view(row) for row in rows], "page": page},
    )


@router.get("/tasks/{task_id}", tags=["tasks"])
def get_task(request: Request, account: WebAccount, task_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    task = _task(db, account.id, task_id)
    etag = _task_etag(task)
    if request.headers.get("If-None-Match", "").strip().strip('"') == etag:
        return Response(status_code=304, headers={"ETag": f'"{etag}"', "Cache-Control": "private, no-store"})
    return _ok(request, task_view(task), headers={"ETag": f'"{etag}"'})


@router.post("/tasks/{task_id}/retry", tags=["tasks"])
def retry_task(
    request: Request,
    account: WebAccount,
    payload: RetryRequest | None = None,
    task_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _write_guard(request, account)
    key = _idempotency_key(request)
    item = _task(db, account.id, task_id)
    previous_retry = db.scalars(
        select(TaskOutbox).where(TaskOutbox.task_id == item.id, TaskOutbox.event_type == "task.retry").order_by(TaskOutbox.created_at.desc())
    ).all()
    if any((row.payload or {}).get("idempotency_key") == key for row in previous_retry):
        return _ok(request, {"task": task_view(item)}, code=202)
    if item.status not in {"failed", "retry_wait"}:
        if item.status in {"queued", "running"}:
            return _ok(request, {"task": task_view(item)}, code=202)
        raise DomainError("TASK_NOT_RETRYABLE", "该任务已经成功或已取消，不能重试", 409)
    item.status = "queued"
    item.failure = None
    item.retry_count += 1
    item.completed_at = None
    db.add(TaskAttempt(task_id=item.id, execution_generation=item.retry_count + 1, status="queued"))
    db.add(TaskOutbox(task_id=item.id, event_type="task.retry", payload={"retry_count": item.retry_count, "reason": payload.reason if payload else "user_retry", "idempotency_key": key}))
    db.commit()
    return _ok(request, {"task": task_view(item)}, code=202)


@router.get("/usage", tags=["usage"])
def get_usage(
    request: Request,
    account: WebAccount,
    feature: str | None = Query(default=None, max_length=32),
    entries_cursor: str | None = Query(default=None, max_length=512),
    entries_limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_verified(account)
    return _ok(
        request,
        usage_view(
            db,
            account,
            feature,
            entries_cursor=entries_cursor,
            entries_limit=entries_limit,
        ),
    )


def _validate_feedback_context(db: Session, account_id: int, context_type: str | None, context_id: str | None) -> None:
    """反馈上下文只允许引用当前账号仍可见的摘要资源。"""

    if not context_type or context_type == "general":
        return
    model_by_type = {
        "analysis": Analysis,
        "rewrite": Rewrite,
        "interview": Interview,
        "export": Export,
    }
    model = model_by_type.get(context_type)
    if model is None or not context_id:
        raise DomainError("FEEDBACK_CONTEXT_INVALID", "反馈上下文不合法", 422)
    row = db.scalar(select(model).where(model.public_id == context_id, model.account_id == account_id))
    if row is None or (hasattr(row, "deleted_at") and row.deleted_at is not None):
        raise NotFoundError("反馈上下文不存在")


@router.post("/feedback", tags=["feedback"], status_code=201)
def create_feedback(payload: FeedbackRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _write_guard(request, account)
    key = _idempotency_key(request)
    _validate_feedback_context(db, account.id, payload.context_type, payload.context_id)
    normalized = payload.model_dump(mode="json")
    normalized["content"] = payload.content.strip()
    request_digest = payload_hash(normalized)
    if key:
        existing = db.scalar(
            select(Feedback).where(
                Feedback.account_id == account.id,
                Feedback.idempotency_key == key,
                Feedback.deleted_at.is_(None),
            )
        )
        if existing is not None:
            if existing.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的反馈不同", 409)
            return _ok(
                request,
                {"id": existing.public_id, "status": existing.status, "created_at": existing.created_at.isoformat()},
            )
    item = Feedback(
        account_id=account.id,
        feedback_type=payload.feedback_type,
        content=payload.content.strip(),
        rating=payload.rating,
        payment_intent=payload.payment_intent,
        context_type=payload.context_type,
        context_id=payload.context_id,
        status="new",
        idempotency_key=key,
        request_hash=request_digest,
    )
    db.add(item)
    db.commit()
    return _ok(request, {"id": item.public_id, "status": item.status, "created_at": item.created_at.isoformat()}, code=201)


@router.get("/stats/me", tags=["stats"])
def personal_stats(
    request: Request,
    account: WebAccount,
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    require_verified(account)
    metric_date = date or shanghai_date()
    try:
        datetime.strptime(metric_date, "%Y-%m-%d")
    except ValueError as exc:
        raise DomainError("STATS_RANGE_INVALID", "统计日期无效", 422) from exc
    local_start = datetime.strptime(metric_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    day_start = local_start.astimezone(timezone.utc)
    day_end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    pool_count = db.scalar(select(func.count(JobPoolItem.id)).where(JobPoolItem.account_id == account.id, JobPoolItem.deleted_at.is_(None))) or 0
    analysis_count = db.scalar(select(func.count(Analysis.id)).where(Analysis.account_id == account.id, Analysis.deleted_at.is_(None), Analysis.status.in_(["available", "succeeded"]), Analysis.completed_at >= day_start, Analysis.completed_at < day_end)) or 0
    interview_count = db.scalar(select(func.count(Interview.id)).where(Interview.account_id == account.id, Interview.deleted_at.is_(None))) or 0
    apply_count = db.scalar(select(func.count(ApplyClick.id)).where(ApplyClick.account_id == account.id, ApplyClick.metric_date == metric_date)) or 0
    adopted_count = db.scalar(select(func.count(RewriteDecision.id)).where(RewriteDecision.account_id == account.id, RewriteDecision.decision.in_(["adopted", "edited"]))) or 0
    if account.registration_role == "seeker":
        metrics = [{"key": "go_to_apply_clicks", "label": "去投递点击数", "value": apply_count, "definition": "Purslyx 成功记录并发起原岗位跳转的次数。"}]
    else:
        metrics = [{"key": "candidate_analyses_completed", "label": "候选人分析次数", "value": analysis_count, "definition": "招聘账号在该上海自然日完成的候选人分析次数。"}]
    return _ok(request, {"date": metric_date, "timezone": "Asia/Shanghai", "registration_role": account.registration_role, "metrics": metrics, "calculated_at": now_utc().isoformat(), "summary": {"job_pool_items": pool_count, "completed_analyses": analysis_count, "interviews": interview_count, "apply_clicks": apply_count, "adopted_rewrites": adopted_count}, "rule_version": "personal-stats-v1"})


# ------------------------------------ 管理端 ------------------------------------


def _masked_email(value: str | None) -> str | None:
    """管理列表只展示可识别但不完整的邮箱摘要。"""

    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    if len(local) <= 1:
        masked = "*"
    elif len(local) == 2:
        masked = f"{local[0]}*"
    else:
        masked = f"{local[0]}{'*' * min(5, len(local) - 2)}{local[-1]}"
    return f"{masked}@{domain}"


def _stats_range(date_from: str | None, date_to: str | None, *, max_days: int = 93) -> tuple[str, str, datetime, datetime]:
    """校验上海自然日范围，并返回 UTC 的半开区间。"""

    today = datetime.strptime(shanghai_date(), "%Y-%m-%d").date()
    try:
        start_date = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else today - timedelta(days=6)
        end_date = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
    except ValueError as exc:
        raise DomainError("STATS_RANGE_INVALID", "统计日期必须是 YYYY-MM-DD", 422) from exc
    if start_date > end_date or (end_date - start_date).days + 1 > max_days:
        raise DomainError("STATS_RANGE_INVALID", f"统计范围不能超过 {max_days} 天且起止顺序必须正确", 422)
    start_local = datetime.combine(start_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai"))
    end_local = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai"))
    return start_date.isoformat(), end_date.isoformat(), start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _role_account_ids(account: Account, db: Session, role: str) -> Any:
    """构造管理统计用的账号子查询；all 不附加身份条件。"""

    if role == "all":
        return None
    if role not in {"seeker", "recruiter"}:
        raise DomainError("STATS_FILTER_INVALID", "注册身份筛选不合法", 422)
    return select(Account.id).where(Account.registration_role == role)


def _admin_guard(request: Request, account: Account, db: Session, permission: str, *, write: bool = False) -> None:
    require_verified(account)
    require_permission(db, account, permission)
    if write:
        require_csrf(request, account)


def _admin_audit(
    db: Session,
    operator: Account,
    action: str,
    target: Account | None,
    request: Request,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            operator_account_id=operator.id,
            target_account_id=target.id if target else None,
            action=action,
            outcome="succeeded",
            reason=reason,
            before_value=before,
            after_value=after,
            request_id=request.headers.get("X-Request-ID"),
            idempotency_key=idempotency_key,
        )
    )


@router.get("/admin/users", tags=["admin"])
def admin_users(
    request: Request,
    account: WebAccount,
    search: str | None = Query(default=None, max_length=160),
    query: str | None = Query(default=None, alias="q", max_length=120),
    registration_role: Literal["seeker", "recruiter"] | None = Query(default=None),
    user_status: Literal["active", "suspended", "pending_verification"] | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.users.read")
    statement = select(Account)
    search_value = (query or search or "").strip()
    if search_value:
        statement = statement.where(Account.email_normalized.ilike(f"%{normalize_email(search_value)}%"))
    if registration_role:
        statement = statement.where(Account.registration_role == registration_role)
    if user_status:
        statement = statement.where(Account.status == user_status)
    rows, page = page_rows(db, statement, Account, cursor=cursor, limit=limit, timestamp_field="created_at")
    return _ok(request, {"items": [{"id": row.public_id, "email": row.email, "registration_role": row.registration_role, "status": row.status, "email_verified": row.email_verified_at is not None, "created_at": row.created_at.isoformat()} for row in rows], "page": page})


@router.get("/admin/users/{user_id}", tags=["admin"])
def admin_user_detail(request: Request, account: WebAccount, user_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.users.read")
    target = _account_or_404(db, user_id)
    return _ok(request, {"account": public_account(db, target), "usage": usage_view(db, target)})


@router.put("/admin/users/{user_id}/status", tags=["admin"])
def admin_update_status(payload: AccountStatusRequest, request: Request, account: WebAccount, user_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.users.manage_status", write=True)
    key = _idempotency_key(request)
    target = _account_or_404(db, user_id)
    if target.id == account.id and payload.status == "suspended":
        raise DomainError("ADMIN_SELF_LOCKOUT", "不能暂停当前正在使用的管理员账号", 409)
    if payload.base_revision is not None and payload.base_revision != target.revision:
        raise DomainError("ADMIN_USER_REVISION_CONFLICT", "用户状态已变化，请刷新后重试", 409, "refresh")
    request_digest = payload_hash({"user_id": user_id, **payload.model_dump(mode="json")})
    if key:
        previous = db.scalar(
            select(AuditEvent).where(
                AuditEvent.operator_account_id == account.id,
                AuditEvent.action == "user.status.update",
                AuditEvent.target_account_id == target.id,
                AuditEvent.idempotency_key == key,
            )
        )
        if previous is not None:
            if (previous.after_value or {}).get("request_hash") != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的用户状态变更不同", 409)
            return _ok(request, {"account": public_account(db, target)})
    before = {"status": target.status}
    target.status = payload.status
    target.revision += 1
    _admin_audit(
        db,
        account,
        "user.status.update",
        target,
        request,
        payload.reason,
        before,
        {"status": target.status, "request_hash": request_digest},
        key,
    )
    if target.status == "suspended":
        revoked_at = now_utc()
        db.query(WebSession).filter(WebSession.account_id == target.id, WebSession.revoked_at.is_(None)).update(
            {WebSession.revoked_at: revoked_at}, synchronize_session=False
        )
        db.query(BrowserSession).filter(
            BrowserSession.account_id == target.id, BrowserSession.revoked_at.is_(None)
        ).update({BrowserSession.revoked_at: revoked_at}, synchronize_session=False)
        db.add(
            SecurityEvent(
                account_id=target.id,
                event_type="all_sessions_revoked",
                outcome="succeeded",
                reason_code="account_suspended",
                client_type="admin",
            )
        )
    db.commit()
    return _ok(request, {"account": public_account(db, target)})


def _role_view(db: Session, role: AdminRole) -> dict[str, Any]:
    permission_keys = [row[0] for row in db.execute(select(AdminPermission.key).join(RolePermission, RolePermission.permission_id == AdminPermission.id).where(RolePermission.role_id == role.id)).all()]
    return {"id": role.public_id, "name": role.name, "description": role.description, "is_builtin": role.is_builtin, "status": role.status, "permissions": sorted(permission_keys), "revision": role.revision}


@router.get("/admin/roles", tags=["admin"])
def list_roles(request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.roles.manage")
    rows = db.scalars(select(AdminRole).order_by(AdminRole.name)).all()
    return _ok(request, {"items": [_role_view(db, row) for row in rows]})


@router.post("/admin/roles", tags=["admin"], status_code=201)
def create_role(payload: RoleRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.roles.manage", write=True)
    operator_permissions = permissions_for(db, account.id)
    if not set(payload.permission_keys).issubset(operator_permissions):
        raise DomainError("ADMIN_PERMISSION_ESCALATION", "不能授予自己没有的后台权限", 403)
    if db.scalar(select(AdminRole).where(AdminRole.name == payload.name.strip())):
        raise DomainError("ADMIN_ROLE_EXISTS", "角色名称已经存在", 409)
    role = AdminRole(name=payload.name.strip(), description=payload.description.strip(), is_builtin=False, status=payload.status)
    db.add(role)
    db.flush()
    _replace_role_permissions(db, role, payload.permission_keys)
    _admin_audit(db, account, "role.create", None, request, "创建后台角色", after={"role": role.name})
    db.commit()
    return _ok(request, _role_view(db, role), code=201)


def _replace_role_permissions(db: Session, role: AdminRole, permission_keys: list[str]) -> None:
    permissions = db.scalars(select(AdminPermission).where(AdminPermission.key.in_(permission_keys))).all() if permission_keys else []
    if len(permissions) != len(set(permission_keys)):
        raise DomainError("ADMIN_PERMISSION_INVALID", "存在未知后台权限", 422)
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    for permission in permissions:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))


@router.put("/admin/roles/{role_id}", tags=["admin"])
def update_role(payload: RoleRequest, request: Request, account: WebAccount, role_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.roles.manage", write=True)
    role = db.scalar(select(AdminRole).where(AdminRole.public_id == role_id))
    if role is None:
        raise NotFoundError("角色不存在")
    if role.is_builtin:
        raise DomainError("ADMIN_ROLE_BUILTIN_READONLY", "内置超级管理员角色不能编辑", 409)
    if payload.base_revision is not None and payload.base_revision != role.revision:
        raise DomainError("ADMIN_ROLE_REVISION_CONFLICT", "角色已变化，请刷新后重试", 409, "refresh")
    if not set(payload.permission_keys).issubset(permissions_for(db, account.id)):
        raise DomainError("ADMIN_PERMISSION_ESCALATION", "不能授予自己没有的后台权限", 403)
    role.name = payload.name.strip()
    role.description = payload.description.strip()
    role.status = payload.status
    role.revision += 1
    _replace_role_permissions(db, role, payload.permission_keys)
    _admin_audit(db, account, "role.update", None, request, "更新后台角色")
    db.commit()
    return _ok(request, _role_view(db, role))


@router.put("/admin/users/{user_id}/roles", tags=["admin"])
def assign_roles(payload: RoleAssignmentRequest, request: Request, account: WebAccount, user_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.roles.manage", write=True)
    target = _account_or_404(db, user_id)
    if target.id == account.id:
        raise DomainError("ADMIN_SELF_ROLE_CHANGE", "不能修改当前管理员自己的后台角色", 409)
    if payload.base_revision is not None and payload.base_revision != target.revision:
        raise DomainError("ADMIN_USER_REVISION_CONFLICT", "用户角色已变化，请刷新后重试", 409, "refresh")
    roles = db.scalars(select(AdminRole).where(AdminRole.public_id.in_(payload.role_ids), AdminRole.status == "active")).all()
    if len(roles) != len(set(payload.role_ids)):
        raise DomainError("ADMIN_ROLE_INVALID", "存在未知或停用角色", 422)
    granted_permissions = {
        row[0]
        for row in db.execute(
            select(AdminPermission.key)
            .join(RolePermission, RolePermission.permission_id == AdminPermission.id)
            .where(RolePermission.role_id.in_([role.id for role in roles]))
        ).all()
    }
    if not granted_permissions.issubset(permissions_for(db, account.id)):
        raise DomainError("ADMIN_PERMISSION_ESCALATION", "不能授予超过自身权限范围的角色", 403)
    builtin_role_ids = set(
        db.scalars(select(AdminRole.id).where(AdminRole.is_builtin.is_(True), AdminRole.status == "active")).all()
    )
    existing_role_ids = set(db.scalars(select(AccountRole.role_id).where(AccountRole.account_id == target.id)).all())
    if existing_role_ids & builtin_role_ids and not (existing_role_ids & builtin_role_ids & {role.id for role in roles}):
        remaining_super_admins = db.scalar(
            select(func.count(AccountRole.account_id))
            .where(AccountRole.role_id.in_(builtin_role_ids), AccountRole.account_id != target.id)
        ) or 0
        if remaining_super_admins < 1:
            raise DomainError("ADMIN_LAST_SUPER_ADMIN", "不能移除最后一个超级管理员", 409)
    db.query(AccountRole).filter(AccountRole.account_id == target.id).delete()
    for role in roles:
        db.add(AccountRole(account_id=target.id, role_id=role.id))
    target.revision += 1
    _admin_audit(db, account, "user.roles.update", target, request, "替换账号后台角色")
    db.commit()
    return _ok(request, {"account": public_account(db, target)})


@router.get("/admin/usage-grants", tags=["admin"])
def admin_list_grants(
    request: Request,
    account: WebAccount,
    feature: str | None = Query(default=None, max_length=32),
    target_account_id: str | None = Query(default=None, alias="account_id", max_length=36),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.usage.grant")
    statement = select(UsageGrant)
    if feature:
        statement = statement.where(UsageGrant.feature == feature)
    if target_account_id:
        statement = statement.where(UsageGrant.account_id == _account_or_404(db, target_account_id).id)
    if created_from:
        statement = statement.where(UsageGrant.created_at >= created_from)
    if created_to:
        statement = statement.where(UsageGrant.created_at < created_to)
    if created_from and created_to and created_from >= created_to:
        raise DomainError("USAGE_RANGE_INVALID", "用量发放时间范围无效", 422)
    rows, page = page_rows(db, statement, UsageGrant, cursor=cursor, limit=limit, timestamp_field="created_at")
    return _ok(request, {"items": [{"id": row.public_id, "account_id": db.get(Account, row.account_id).public_id if db.get(Account, row.account_id) else None, "feature": row.feature, "count": row.count, "reason": row.reason, "before_available": row.before_available, "after_available": row.after_available, "created_at": row.created_at.isoformat()} for row in rows], "page": page})


@router.post("/admin/users/{user_id}/usage-grants", tags=["admin"], status_code=201)
def admin_grant(payload: GrantRequest, request: Request, account: WebAccount, user_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.usage.grant", write=True)
    key = _idempotency_key(request)
    target = _account_or_404(db, user_id)
    grant, existed = grant_feature(db, target, payload.feature, payload.count, source_type="admin_grant", reason=payload.reason, operator_account_id=account.id, idempotency_key=key)
    _admin_audit(db, account, "usage.grant", target, request, payload.reason, after={"feature": payload.feature, "count": payload.count})
    db.commit()
    balance = next(item for item in usage_view(db, target)["balances"] if item["feature"] == payload.feature)
    return _ok(request, {"grant": {"id": grant.public_id, "feature": grant.feature, "count": grant.count, "reason": grant.reason, "before_available": grant.before_available, "after_available": grant.after_available, "created_at": grant.created_at.isoformat()}, "balance": balance}, code=200 if existed else 201)


class FeedbackStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["new", "reviewed", "closed", "reviewing"]
    base_revision: int | None = Field(default=None, ge=1)
    reason: str = Field(default="更新产品反馈状态", min_length=1, max_length=300)


class LogExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    log_type: Literal["operations", "security", "tasks"] | None = None
    log_category: Literal["admin_operations", "security", "task_runs"] | None = None
    export_format: Literal["csv", "jsonl"] = "jsonl"
    filters: dict[str, Any] = Field(default_factory=dict)
    filter: dict[str, Any] | None = None

    @model_validator(mode="after")
    def normalize_log_type(self) -> "LogExportRequest":
        if self.log_type is None and self.log_category is None:
            raise ValueError("需要日志分类")
        if self.log_type is None:
            self.log_type = {"admin_operations": "operations", "security": "security", "task_runs": "tasks"}[self.log_category or "security"]
        if self.filter is not None:
            self.filters = self.filter
        return self


def _log_permission(log_type: str) -> str:
    permission = {
        "operations": "admin.logs.operations.read",
        "security": "admin.logs.security.read",
        "tasks": "admin.logs.tasks.read",
    }.get(log_type)
    if permission is None:
        raise DomainError("LOG_TYPE_INVALID", "日志分类不合法", 422)
    return permission


@router.get("/admin/metrics", tags=["admin"])
def admin_metrics(
    request: Request,
    account: WebAccount,
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    registration_role: Literal["all", "seeker", "recruiter"] = "all",
    feature: Literal["all", "analysis", "rewrite", "interview", "import"] = "all",
    granularity: Literal["day"] = "day",
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.stats.read")
    start_label, end_label, start_at, end_at = _stats_range(date_from, date_to)
    account_ids = _role_account_ids(account, db, registration_role)
    task_types = {
        "analysis": {"analysis"},
        "rewrite": {"rewrite"},
        "interview": {"interview_opening", "interview_feedback", "interview_turn", "interview_summary"},
        "import": {"document_parse"},
    }.get(feature)

    def scoped(model: Any, *conditions: Any) -> Any:
        statement = select(model).where(*conditions)
        if account_ids is not None and hasattr(model, "account_id"):
            statement = statement.where(model.account_id.in_(account_ids))
        return statement

    def count_rows(model: Any, *conditions: Any) -> int:
        statement = select(func.count(model.id)).where(*conditions)
        if account_ids is not None and hasattr(model, "account_id"):
            statement = statement.where(model.account_id.in_(account_ids))
        return int(db.scalar(statement) or 0)

    task_conditions = [Task.created_at >= start_at, Task.created_at < end_at]
    if task_types is not None:
        task_conditions.append(Task.task_type.in_(task_types))
    task_rows = db.scalars(scoped(Task, *task_conditions).order_by(Task.created_at)).all()
    succeeded = [row for row in task_rows if row.status == "succeeded"]
    failed = [row for row in task_rows if row.status == "failed"]
    if feature in {"all", "analysis", "rewrite", "interview", "import"}:
        usage_statement = select(func.coalesce(func.sum(UsageLedger.amount), 0)).where(
            UsageLedger.event_type == "settle",
            UsageLedger.created_at >= start_at,
            UsageLedger.created_at < end_at,
        )
        if account_ids is not None:
            usage_statement = usage_statement.where(UsageLedger.account_id.in_(account_ids))
        if feature != "all":
            usage_statement = usage_statement.where(UsageLedger.feature == ("analysis" if feature == "import" else feature))
        usage_settled = int(db.scalar(usage_statement) or 0)
    else:
        usage_settled = 0
    click_statement = select(func.count(ApplyClick.id)).where(ApplyClick.metric_date >= start_label, ApplyClick.metric_date <= end_label)
    if account_ids is not None:
        click_statement = click_statement.where(ApplyClick.account_id.in_(account_ids))
    apply_clicks = int(db.scalar(click_statement) or 0) if feature in {"all", "analysis"} else 0
    rewrite_count = count_rows(RewriteDecision, RewriteDecision.created_at >= start_at, RewriteDecision.created_at < end_at, RewriteDecision.decision.in_(["adopt", "adopted", "edited"])) if feature in {"all", "rewrite"} else 0
    export_count = count_rows(Export, Export.created_at >= start_at, Export.created_at < end_at) if feature in {"all", "import"} else 0
    completed_interviews = count_rows(Interview, Interview.updated_at >= start_at, Interview.updated_at < end_at, Interview.status == "completed") if feature in {"all", "interview"} else 0
    ended_interviews = count_rows(Interview, Interview.updated_at >= start_at, Interview.updated_at < end_at, Interview.status == "ended_early") if feature in {"all", "interview"} else 0
    feedback_count = count_rows(Feedback, Feedback.created_at >= start_at, Feedback.created_at < end_at)
    positive_payment_count = count_rows(Feedback, Feedback.created_at >= start_at, Feedback.created_at < end_at, Feedback.payment_intent.in_(["willing", "depends_on_price"]))
    account_conditions = [Account.created_at >= start_at, Account.created_at < end_at]
    if account_ids is not None:
        account_conditions.append(Account.id.in_(account_ids))
    registered_accounts = int(db.scalar(select(func.count(Account.id)).where(*account_conditions)) or 0)
    active_conditions = [*account_conditions, Account.status == "active"]
    active_accounts = int(db.scalar(select(func.count(Account.id)).where(*active_conditions)) or 0)

    def duration_ms(row: Task) -> int | None:
        if row.started_at is None or row.completed_at is None:
            return None
        return max(0, int((row.completed_at - row.started_at).total_seconds() * 1000))

    def p95(values: list[int]) -> int | None:
        if not values:
            return None
        values = sorted(values)
        return values[min(len(values) - 1, math.ceil(len(values) * 0.95) - 1)]

    def task_metric(rows: list[Task]) -> dict[str, Any]:
        durations = [value for value in (duration_ms(row) for row in rows if row.status == "succeeded") if value is not None]
        return {
            "tasks_succeeded": sum(row.status == "succeeded" for row in rows),
            "tasks_failed": sum(row.status == "failed" for row in rows),
            "task_retries": sum(row.retry_count for row in rows),
            "average_duration_ms": round(sum(durations) / len(durations), 2) if durations else None,
            "p95_duration_ms": p95(durations),
            "sample_count": len(durations),
        }

    series: list[dict[str, Any]] = []
    current_date = datetime.strptime(start_label, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_label, "%Y-%m-%d").date()
    while current_date <= end_date:
        day_label = current_date.isoformat()
        day_start = datetime.combine(current_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
        day_end = day_start + timedelta(days=1)
        day_rows = [row for row in task_rows if row.created_at >= day_start and row.created_at < day_end]
        row = task_metric(day_rows)
        row.update({"date": day_label, "usage_settled": 0, "go_to_apply_clicks": 0})
        row["usage_settled"] = sum(
            int(value.amount)
            for value in db.scalars(
                select(UsageLedger).where(UsageLedger.event_type == "settle", UsageLedger.created_at >= day_start, UsageLedger.created_at < day_end, *( [UsageLedger.account_id.in_(account_ids)] if account_ids is not None else []), *( [UsageLedger.feature == ("analysis" if feature == "import" else feature)] if feature != "all" else []))
            ).all()
        ) if feature in {"all", "analysis", "rewrite", "interview", "import"} else 0
        click_day = select(func.count(ApplyClick.id)).where(ApplyClick.metric_date == day_label, *( [ApplyClick.account_id.in_(account_ids)] if account_ids is not None else []))
        row["go_to_apply_clicks"] = int(db.scalar(click_day) or 0) if feature in {"all", "analysis"} else 0
        series.append(row)
        current_date += timedelta(days=1)

    totals = {
        "registered_accounts": registered_accounts,
        "active_accounts": active_accounts,
        "tasks_succeeded": len(succeeded),
        "tasks_failed": len(failed),
        "task_retries": sum(row.retry_count for row in task_rows),
        "usage_settled": usage_settled,
        "go_to_apply_clicks": apply_clicks,
        "rewrite_adopted_segments": rewrite_count,
        "resume_exports": export_count,
        "interviews_completed": completed_interviews,
        "interviews_ended_early": ended_interviews,
        "feedback_submitted": feedback_count,
        "payment_intent_positive": positive_payment_count,
    }
    return _ok(
        request,
        {
            "timezone": "Asia/Shanghai",
            "date_from": start_label,
            "date_to": end_label,
            "registration_role": registration_role,
            "feature": feature,
            "granularity": granularity,
            "rule_version": "daily-metrics-v1",
            "source_watermark_at": now_utc().isoformat(),
            "totals": totals,
            # 兼容最小演示页面的旧字段，同时给正式管理端提供完整口径。
            "metric_date": end_label,
            "accounts": {"total": registered_accounts, "active": active_accounts},
            "job_pool_items": db.scalar(select(func.count(JobPoolItem.id)).where(JobPoolItem.deleted_at.is_(None))) or 0,
            "analyses": len([row for row in succeeded if row.task_type == "analysis"]),
            "interviews": completed_interviews + ended_interviews,
            "apply_clicks_today": apply_clicks if end_label == shanghai_date() else 0,
            "series": series,
        },
    )


@router.get("/admin/costs", tags=["admin"])
def admin_costs(
    request: Request,
    account: WebAccount,
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    registration_role: Literal["all", "seeker", "recruiter"] = "all",
    feature: Literal["all", "analysis", "rewrite", "interview", "import"] = "all",
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.stats.read")
    _, _, start_at, end_at = _stats_range(date_from, date_to)
    account_ids = _role_account_ids(account, db, registration_role)
    conditions: list[Any] = [ModelCall.created_at >= start_at, ModelCall.created_at < end_at]
    if account_ids is not None:
        conditions.append(ModelCall.account_id.in_(account_ids))
    if feature != "all":
        conditions.append(ModelCall.feature == ("document_parse" if feature == "import" else feature))
    calls = db.scalars(select(ModelCall).where(*conditions)).all()
    known = [row.cost_usd for row in calls if row.cost_usd is not None]
    successful_result_count = int(db.scalar(select(func.count(Analysis.id)).where(Analysis.status.in_(["available", "succeeded"]), Analysis.completed_at >= start_at, Analysis.completed_at < end_at, *( [Analysis.account_id.in_(account_ids)] if account_ids is not None else []))) or 0)
    known_total = sum(known)
    by_feature = []
    for value in sorted({row.feature for row in calls}):
        feature_total = sum((row.cost_usd for row in calls if row.feature == value and row.cost_usd is not None), 0)
        by_feature.append(
            {
                "feature": value,
                "calls": sum(row.feature == value for row in calls),
                "cost_usd": float(feature_total) if feature_total else None,
                "cost_usd_exact": f"{feature_total:.8f}" if feature_total else None,
            }
        )
    return _ok(request, {"known_cost": f"{known_total:.8f}" if known else None, "known_cost_usd": float(known_total) if known else None, "currency": "USD", "unknown_cost_count": sum(row.cost_usd is None for row in calls), "unknown_cost_calls": sum(row.cost_usd is None for row in calls), "model_call_count": len(calls), "successful_result_count": successful_result_count, "known_cost_per_success": f"{known_total / successful_result_count:.8f}" if known and successful_result_count else None, "by_feature": by_feature, "pricing_watermark_at": now_utc().isoformat(), "rule_version": "costs-v1"})


@router.get("/admin/feedback", tags=["admin"])
def admin_feedback(
    request: Request,
    account: WebAccount,
    feedback_type: str | None = Query(default=None, max_length=40),
    feedback_status: str | None = Query(default=None, alias="status", max_length=20),
    payment_intent: str | None = Query(default=None, max_length=32),
    registration_role: Literal["seeker", "recruiter"] | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.stats.read")
    statement = select(Feedback).where(Feedback.deleted_at.is_(None))
    if feedback_type:
        statement = statement.where(Feedback.feedback_type == feedback_type)
    if feedback_status:
        statement = statement.where(Feedback.status == feedback_status)
    if payment_intent:
        statement = statement.where(Feedback.payment_intent == payment_intent)
    if registration_role:
        statement = statement.where(Feedback.account_id.in_(select(Account.id).where(Account.registration_role == registration_role)))
    if created_from:
        statement = statement.where(Feedback.created_at >= created_from)
    if created_to:
        statement = statement.where(Feedback.created_at < created_to)
    if created_from and created_to and created_from >= created_to:
        raise DomainError("FEEDBACK_RANGE_INVALID", "反馈时间范围无效", 422)
    rows, page = page_rows(db, statement, Feedback, cursor=cursor, limit=limit, timestamp_field="created_at")
    return _ok(request, {"items": [{"id": row.public_id, "account": _masked_email(db.get(Account, row.account_id).email if db.get(Account, row.account_id) else None), "account_id": db.get(Account, row.account_id).public_id if db.get(Account, row.account_id) else None, "feedback_type": row.feedback_type, "rating": row.rating, "payment_intent": row.payment_intent, "content_preview": row.content[:160], "context_type": row.context_type, "status": row.status, "revision": row.revision, "created_at": row.created_at.isoformat()} for row in rows], "page": page})


@router.get("/admin/feedback/{feedback_id}", tags=["admin"])
def admin_feedback_detail(request: Request, account: WebAccount, feedback_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.stats.read")
    item = db.scalar(select(Feedback).where(Feedback.public_id == feedback_id, Feedback.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("反馈不存在")
    return _ok(request, {"id": item.public_id, "feedback_type": item.feedback_type, "content": item.content, "rating": item.rating, "payment_intent": item.payment_intent, "context_type": item.context_type, "context_id": item.context_id, "status": item.status, "created_at": item.created_at.isoformat()})


@router.put("/admin/feedback/{feedback_id}", tags=["admin"])
def admin_update_feedback(payload: FeedbackStatusRequest, request: Request, account: WebAccount, feedback_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.stats.read", write=True)
    key = _idempotency_key(request)
    item = db.scalar(select(Feedback).where(Feedback.public_id == feedback_id, Feedback.deleted_at.is_(None)))
    if item is None:
        raise NotFoundError("反馈不存在")
    if payload.base_revision is not None and payload.base_revision != item.revision:
        raise DomainError("FEEDBACK_STATUS_CONFLICT", "反馈状态已变化，请刷新后重试", 409, "refresh")
    normalized_status = "reviewed" if payload.status == "reviewing" else payload.status
    if item.status == "closed" and normalized_status != "closed":
        raise DomainError("FEEDBACK_STATUS_CONFLICT", "已关闭反馈不能重新打开", 409)
    request_digest = payload_hash({"feedback_id": feedback_id, **payload.model_dump(mode="json"), "status": normalized_status})
    existing_audit = db.scalar(select(AuditEvent).where(AuditEvent.operator_account_id == account.id, AuditEvent.action == "feedback.status.update", AuditEvent.idempotency_key == key)) if key else None
    if existing_audit is not None:
        if (existing_audit.after_value or {}).get("request_hash") != request_digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的反馈状态变更不同", 409)
        return _ok(request, {"id": item.public_id, "status": item.status, "revision": item.revision})
    before = {"status": item.status}
    item.status = normalized_status
    item.revision += 1
    item.reviewed_by_account_id = account.id
    item.reviewed_at = now_utc()
    _admin_audit(db, account, "feedback.status.update", None, request, payload.reason, before, {"status": item.status, "revision": item.revision, "request_hash": request_digest}, key)
    db.commit()
    return _ok(request, {"id": item.public_id, "status": item.status, "revision": item.revision})


def _log_items(
    db: Session,
    log_type: str,
    *,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    account_id: str | None = None,
    operator_id: str | None = None,
    target_id: str | None = None,
    target_type: str | None = None,
    result: str | None = None,
    request_id: str | None = None,
    action: str | None = None,
    event_type: str | None = None,
    task_type: str | None = None,
    task_status: str | None = None,
    retry_count_min: int | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按分类读取脱敏日志摘要；任何日志列表都不返回请求正文。"""

    if log_type == "operations":
        model = AuditEvent
    elif log_type == "security":
        model = SecurityEvent
    elif log_type == "tasks":
        model = Task
    else:
        raise DomainError("LOG_TYPE_INVALID", "日志分类不合法", 422)
    effective_to = created_to or now_utc()
    effective_from = created_from or effective_to - timedelta(hours=24)
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=timezone.utc)
    if effective_to.tzinfo is None:
        effective_to = effective_to.replace(tzinfo=timezone.utc)
    if effective_from >= effective_to or effective_to - effective_from > timedelta(days=31):
        raise DomainError("LOG_RANGE_INVALID", "日志导出或查询范围不能超过 31 天且起止顺序必须正确", 422)
    statement = select(model)
    statement = statement.where(model.created_at >= effective_from, model.created_at < effective_to)
    if account_id:
        target = db.scalar(select(Account.id).where(Account.public_id == account_id))
        if target is None:
            statement = statement.where(False)
        elif log_type == "operations":
            statement = statement.where((AuditEvent.operator_account_id == target) | (AuditEvent.target_account_id == target))
        else:
            statement = statement.where(model.account_id == target)
    if operator_id:
        operator = db.scalar(select(Account.id).where(Account.public_id == operator_id))
        if operator is None or log_type != "operations":
            statement = statement.where(False)
        else:
            statement = statement.where(AuditEvent.operator_account_id == operator)
    if target_id:
        target_account = db.scalar(select(Account.id).where(Account.public_id == target_id))
        if target_account is None or log_type != "operations":
            statement = statement.where(False)
        else:
            statement = statement.where(AuditEvent.target_account_id == target_account)
    if target_type:
        if target_type != "account" or log_type != "operations":
            statement = statement.where(False)
    if result:
        if log_type in {"operations", "security"} and result not in {"succeeded", "denied", "failed"}:
            raise DomainError("LOG_FILTER_INVALID", "日志结果筛选不合法", 422)
        if log_type == "tasks" and result not in {"queued", "running", "retry_wait", "needs_input", "succeeded", "failed", "cancelled"}:
            raise DomainError("LOG_FILTER_INVALID", "任务状态筛选不合法", 422)
        if log_type == "operations":
            statement = statement.where(AuditEvent.outcome == result)
        elif log_type == "security":
            statement = statement.where(SecurityEvent.outcome == result)
        else:
            statement = statement.where(Task.status == result)
    if request_id:
        if log_type == "operations":
            statement = statement.where(AuditEvent.request_id == request_id)
        elif log_type == "security":
            statement = statement.where(SecurityEvent.request_id == request_id)
    if action and log_type == "operations":
        statement = statement.where(AuditEvent.action == action)
    if event_type and log_type == "security":
        statement = statement.where(SecurityEvent.event_type == event_type)
    if task_type and log_type == "tasks":
        statement = statement.where(Task.task_type == task_type)
    if task_status and log_type == "tasks":
        statement = statement.where(Task.status == task_status)
    if retry_count_min is not None and log_type == "tasks":
        statement = statement.where(Task.retry_count >= retry_count_min)
    rows, page = page_rows(db, statement, model, cursor=cursor, limit=limit, timestamp_field="created_at")
    items: list[dict[str, Any]] = []
    for row in rows:
        if log_type == "operations":
            target_account = db.get(Account, row.target_account_id) if row.target_account_id else None
            items.append({"id": row.public_id, "category": "operations", "action": row.action, "outcome": row.outcome, "reason": row.reason, "target_account": _masked_email(target_account.email if target_account else None), "target_account_id": target_account.public_id if target_account else None, "request_id": row.request_id, "created_at": row.created_at.isoformat()})
        elif log_type == "security":
            event_account = db.get(Account, row.account_id) if row.account_id else None
            items.append({"id": row.public_id, "category": "security", "event_type": row.event_type, "outcome": row.outcome, "reason_code": row.reason_code, "account": _masked_email(event_account.email if event_account else None), "account_id": event_account.public_id if event_account else None, "request_id": row.request_id, "client_type": row.client_type, "created_at": row.created_at.isoformat()})
        else:
            failure = row.failure or {}
            items.append({"id": row.public_id, "category": "tasks", "task_type": row.task_type, "status": row.status, "failure_code": failure.get("code"), "retryable": bool(failure.get("retryable")), "retry_count": row.retry_count, "created_at": row.created_at.isoformat(), "started_at": row.started_at.isoformat() if row.started_at else None, "completed_at": row.completed_at.isoformat() if row.completed_at else None})
    return items, page


@router.get("/admin/logs/operations", tags=["logs"])
def admin_operation_logs(
    request: Request,
    account: WebAccount,
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    account_id: str | None = Query(default=None, max_length=36),
    operator_id: str | None = Query(default=None, max_length=36),
    target_id: str | None = Query(default=None, max_length=36),
    target_type: str | None = Query(default=None, max_length=40),
    result: str | None = Query(default=None, max_length=24),
    request_id: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=100),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.logs.operations.read")
    items, page = _log_items(db, "operations", created_from=created_from, created_to=created_to, account_id=account_id, operator_id=operator_id, target_id=target_id, target_type=target_type, result=result, request_id=request_id, action=action, cursor=cursor, limit=limit)
    return _ok(request, {"items": items, "page": page})


@router.get("/admin/logs/security", tags=["logs"])
def admin_security_logs(
    request: Request,
    account: WebAccount,
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    account_id: str | None = Query(default=None, max_length=36),
    result: str | None = Query(default=None, max_length=24),
    request_id: str | None = Query(default=None, max_length=64),
    event_type: str | None = Query(default=None, max_length=80),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.logs.security.read")
    items, page = _log_items(db, "security", created_from=created_from, created_to=created_to, account_id=account_id, result=result, request_id=request_id, event_type=event_type, cursor=cursor, limit=limit)
    return _ok(request, {"items": items, "page": page})


@router.get("/admin/logs/tasks", tags=["logs"])
def admin_task_logs(
    request: Request,
    account: WebAccount,
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    account_id: str | None = Query(default=None, max_length=36),
    result: str | None = Query(default=None, max_length=24),
    request_id: str | None = Query(default=None, max_length=64),
    task_type: str | None = Query(default=None, max_length=40),
    task_status: str | None = Query(default=None, max_length=24),
    retry_count_min: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    _admin_guard(request, account, db, "admin.logs.tasks.read")
    items, page = _log_items(db, "tasks", created_from=created_from, created_to=created_to, account_id=account_id, result=result, request_id=request_id, task_type=task_type, task_status=task_status, retry_count_min=retry_count_min, cursor=cursor, limit=limit)
    return _ok(request, {"items": items, "page": page})


@router.get("/admin/logs/{log_type}/{log_id}", tags=["logs"])
def admin_log_detail(request: Request, account: WebAccount, log_type: str, log_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    if log_type not in {"operations", "security", "tasks"}:
        raise DomainError("LOG_TYPE_INVALID", "日志分类不合法", 422)
    _admin_guard(request, account, db, _log_permission(log_type))
    if log_type == "operations":
        row = db.scalar(select(AuditEvent).where(AuditEvent.public_id == log_id))
        operator = db.get(Account, row.operator_account_id) if row and row.operator_account_id else None
        target = db.get(Account, row.target_account_id) if row and row.target_account_id else None
        value = {"id": row.public_id, "category": "operations", "operator": {"id": operator.public_id, "email_masked": _masked_email(operator.email)} if operator else None, "target": {"type": "account", "id": target.public_id, "email_masked": _masked_email(target.email)} if target else None, "action": row.action, "result": row.outcome, "reason": row.reason, "before": row.before_value, "after": row.after_value, "request_id": row.request_id, "created_at": row.created_at.isoformat()} if row else None
    elif log_type == "security":
        row = db.scalar(select(SecurityEvent).where(SecurityEvent.public_id == log_id))
        event_account = db.get(Account, row.account_id) if row and row.account_id else None
        value = {"id": row.public_id, "category": "security", "event_type": row.event_type, "result": row.outcome, "reason_code": row.reason_code, "account": {"id": event_account.public_id, "email_masked": _masked_email(event_account.email)} if event_account else None, "client_type": row.client_type, "request_id": row.request_id, "created_at": row.created_at.isoformat()} if row else None
    else:
        row = db.scalar(select(Task).where(Task.public_id == log_id))
        if row:
            failure = row.failure or {}
            calls = db.scalars(select(ModelCall).where(ModelCall.task_id == row.id).order_by(ModelCall.created_at)).all()
            value = {"id": row.public_id, "category": "tasks", "task_type": row.task_type, "status": row.status, "created_at": row.created_at.isoformat(), "started_at": row.started_at.isoformat() if row.started_at else None, "completed_at": row.completed_at.isoformat() if row.completed_at else None, "queue_duration_ms": int((row.started_at - row.created_at).total_seconds() * 1000) if row.started_at else None, "execution_duration_ms": int((row.completed_at - row.started_at).total_seconds() * 1000) if row.completed_at and row.started_at else None, "retry_count": row.retry_count, "failure_code": failure.get("code"), "retryable": bool(failure.get("retryable")), "model_calls": [{"id": call.public_id, "provider": call.provider, "model": call.model, "status": call.status, "input_tokens": call.input_tokens, "output_tokens": call.output_tokens, "cost_usd": float(call.cost_usd) if call.cost_usd is not None else None, "cost_usd_exact": f"{call.cost_usd:.8f}" if call.cost_usd is not None else None, "error_code": call.error_code, "duration_ms": call.duration_ms, "created_at": call.created_at.isoformat()} for call in calls], "result": row.result if row.result and isinstance(row.result, dict) and set(row.result).issubset({"resource_type", "resource_id", "path", "export_id", "file_ready", "question_id", "needs_followup", "document_id", "draft_id"}) else None}
        else:
            value = None
    if value is None:
        raise NotFoundError("日志不存在")
    return _ok(request, value)


def _escape_csv_formula(value: Any) -> str:
    """防止导出的 CSV 被电子表格当成公式执行。"""

    text_value = "" if value is None else str(value)
    return f"'{text_value}" if text_value.startswith(("=", "+", "-", "@")) else text_value


@router.post("/admin/log-exports", tags=["logs"], status_code=201)
def create_log_export(payload: LogExportRequest, request: Request, account: WebAccount, db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.logs.export", write=True)
    key = _idempotency_key(request)
    normalized_payload = payload.model_dump(mode="json")
    request_digest = payload_hash(normalized_payload)
    if key:
        existing = db.scalar(select(LogExport).where(LogExport.account_id == account.id, LogExport.idempotency_key == key))
        if existing is not None:
            if existing.request_hash != request_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应的日志导出不同", 409)
            return _ok(request, {"id": existing.public_id, "status": existing.status, "format": existing.filters.get("export_format", "jsonl"), "created_at": existing.created_at.isoformat()})

    filters = dict(payload.filters or {})

    def optional_datetime(name: str) -> datetime | None:
        value = filters.get(name)
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise DomainError("LOG_RANGE_INVALID", f"{name} 必须是 ISO 时间", 422)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DomainError("LOG_RANGE_INVALID", f"{name} 必须是 ISO 时间", 422) from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    created_from = optional_datetime("created_from")
    created_to = optional_datetime("created_to")
    if created_from and created_to and (created_from >= created_to or created_to - created_from > timedelta(days=31)):
        raise DomainError("LOG_RANGE_INVALID", "日志导出范围不能超过 31 天且起止顺序必须正确", 422)
    log_type = payload.log_type or "operations"
    _admin_guard(request, account, db, _log_permission(log_type), write=True)
    list_kwargs = {
        "created_from": created_from,
        "created_to": created_to,
        "account_id": filters.get("account_id"),
        "result": filters.get("result"),
        "request_id": filters.get("request_id"),
        "action": filters.get("action"),
        "event_type": filters.get("event_type"),
        "task_type": filters.get("task_type"),
        "task_status": filters.get("task_status"),
        "retry_count_min": filters.get("retry_count_min"),
    }
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        chunk, page = _log_items(db, log_type, **list_kwargs, cursor=cursor, limit=100)
        rows.extend(chunk)
        if len(rows) > 100_000:
            raise DomainError("LOG_RANGE_INVALID", "导出结果超过 100,000 行，请缩小范围", 422)
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")
        if not cursor:
            break
    for row in rows:
        row.pop("category", None)

    output_format = payload.export_format
    output_suffix = "csv" if output_format == "csv" else "jsonl"
    output_path = settings.export_dir.resolve() / f"log-{secrets.token_hex(12)}.{output_suffix}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.tmp")
    published_path: Path | None = None
    try:
        if output_format == "csv":
            columns = sorted({key_name for row in rows for key_name in row}) or ["id"]
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key_name: _escape_csv_formula(row.get(key_name)) for key_name in columns})
            temporary_path.write_text("\ufeff" + buffer.getvalue(), encoding="utf-8")
        else:
            temporary_path.write_text("".join(json.dumps({"schema_version": "log-export-v1", **row}, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        byte_size = temporary_path.stat().st_size
        ensure_storage_capacity(db, byte_size)
        temporary_path.replace(output_path)
        published_path = output_path
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    stored_filters = {**filters, "export_format": output_format, "row_count": len(rows)}
    try:
        item = LogExport(account_id=account.id, log_type=log_type, filters=stored_filters, status="downloadable", file_path=storage_key(output_path, settings.data_dir), expires_at=now_utc() + timedelta(days=1), idempotency_key=key, request_hash=request_digest)
        db.add(item)
        db.add(
            StoredFile(
                account_id=account.id,
                purpose="log_export",
                original_filename=f"purslyx-logs.{output_format}",
                media_type="text/csv" if output_format == "csv" else "application/x-ndjson",
                byte_size=byte_size,
                sha256=sha256_bytes(output_path.read_bytes()),
                storage_key=storage_key(output_path, settings.data_dir),
                status="available",
            )
        )
        _admin_audit(db, account, "logs.export", None, request, "创建日志导出", after={"log_type": log_type, "format": output_format, "row_count": len(rows)}, idempotency_key=key)
        db.commit()
    except Exception:
        db.rollback()
        if published_path is not None:
            published_path.unlink(missing_ok=True)
        raise
    return _ok(request, {"id": item.public_id, "status": item.status, "format": output_format, "row_count": len(rows), "created_at": item.created_at.isoformat()}, code=201)


def _expire_log_export(db: Session, item: LogExport) -> None:
    item.status = "expired"
    if item.file_path:
        db.query(StoredFile).filter(
            StoredFile.account_id == item.account_id,
            StoredFile.purpose == "log_export",
            StoredFile.storage_key == item.file_path,
            StoredFile.status == "available",
            StoredFile.deleted_at.is_(None),
        ).update({StoredFile.status: "deleted", StoredFile.deleted_at: now_utc()}, synchronize_session=False)
    db.commit()


@router.get("/admin/log-exports/{export_id}", tags=["logs"])
def get_log_export(request: Request, account: WebAccount, export_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> JSONResponse:
    _admin_guard(request, account, db, "admin.logs.export")
    item = db.scalar(select(LogExport).where(LogExport.public_id == export_id, LogExport.account_id == account.id))
    if item is None:
        raise NotFoundError("日志导出不存在")
    _admin_guard(request, account, db, _log_permission(item.log_type))
    if item.expires_at and item.expires_at <= now_utc():
        _expire_log_export(db, item)
        raise DomainError("LOG_EXPORT_EXPIRED", "日志导出已过期", 410)
    return _ok(request, {"id": item.public_id, "log_type": item.log_type, "status": item.status, "format": item.filters.get("export_format", "jsonl"), "row_count": item.filters.get("row_count"), "filters": {key: value for key, value in item.filters.items() if key not in {"row_count", "export_format"}}, "expires_at": item.expires_at.isoformat() if item.expires_at else None})


@router.get("/admin/log-exports/{export_id}/file", tags=["logs"])
def get_log_export_file(request: Request, account: WebAccount, export_id: str = PathParam(min_length=1, max_length=36), db: Session = Depends(get_db)) -> Response:
    _admin_guard(request, account, db, "admin.logs.export")
    item = db.scalar(select(LogExport).where(LogExport.public_id == export_id, LogExport.account_id == account.id))
    if item is None:
        raise NotFoundError("日志导出不存在或已过期")
    _admin_guard(request, account, db, _log_permission(item.log_type))
    if item.expires_at and item.expires_at <= now_utc():
        _expire_log_export(db, item)
        raise DomainError("LOG_EXPORT_EXPIRED", "日志导出已过期", 410)
    file_path = private_path(item.file_path, settings.data_dir) if item.file_path else None
    if item.status not in {"available", "downloadable"} or file_path is None or not file_path.is_file():
        raise DomainError("LOG_EXPORT_NOT_READY", "日志导出尚未可下载", 409, "wait")
    stored = db.scalar(
        select(StoredFile).where(
            StoredFile.account_id == account.id,
            StoredFile.purpose == "log_export",
            StoredFile.storage_key == item.file_path,
            StoredFile.status == "available",
            StoredFile.deleted_at.is_(None),
        )
    )
    if stored is not None and not private_path(stored.storage_key, settings.data_dir).is_file():
        raise DomainError("LOG_EXPORT_NOT_READY", "日志导出尚未可下载", 409, "wait")
    _admin_audit(db, account, "logs.download", None, request, "下载日志导出", after={"export_id": item.public_id})
    db.commit()
    output_format = item.filters.get("export_format", "jsonl")
    response = FileResponse(file_path, filename=f"purslyx-logs.{output_format}", media_type="text/csv" if output_format == "csv" else "application/x-ndjson")
    response.headers["Cache-Control"] = "private, no-store"
    return response
