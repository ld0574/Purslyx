"""Purslyx 最小可演示服务。

这里刻意只实现一条可落地的纵向链路：文字资料 → 确认版本 → 匹配报告。路由使用
``/api/v1/demo`` 前缀，方便明天演示 SDD 时把“需求、接口、实现、测试”串起来，也明确
它不是完整生产账号系统的替代品。
"""

from __future__ import annotations

import secrets
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Path as PathParam, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import settings
from .db import engine, get_db, init_db
from .errors import DomainError, NotFoundError
from .api import router as api_router
from .model_provider import get_model_provider
from .models import Account, Analysis, Document, DocumentVersion
from .security import hash_password


DEMO_EMAIL = "demo@purslyx.local"
WEB_INDEX = Path(__file__).resolve().parents[2] / "web" / "index.html"


def utcnow() -> datetime:
    """返回带时区的 UTC 时间，避免把本机时区写入业务记录。"""

    return datetime.now(timezone.utc)


class DocumentCreateRequest(BaseModel):
    """最小演示接口只接收文字，文件上传留到下一轮 Feature Spec。"""

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["resume", "job"]
    title: str = Field(default="未命名资料", min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=100_000)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("资料正文不能为空")
        return value


class MatchCreateRequest(BaseModel):
    """匹配请求引用已确认版本，不接受客户端直接传综合分。"""

    model_config = ConfigDict(extra="forbid")

    resume_version_id: str = Field(min_length=1, max_length=36)
    job_version_id: str = Field(min_length=1, max_length=36)
    preference: dict[str, Any] | None = None


def _meta(request: Request) -> dict[str, str]:
    """为所有响应生成轻量元信息，便于演示时追踪一次请求。"""

    request_id = request.headers.get("X-Request-ID", "").strip()
    if not request_id or len(request_id) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", request_id):
        request_id = secrets.token_hex(12)
    request.state.request_id = request_id
    return {"request_id": request_id, "server_time": utcnow().isoformat()}


def _ok(request: Request, data: Any, *, code: int = status.HTTP_200_OK) -> JSONResponse:
    meta = _meta(request)
    response = JSONResponse(status_code=code, content={"data": data, "meta": meta})
    response.headers["X-Request-ID"] = meta["request_id"]
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _error(request: Request, error: DomainError) -> JSONResponse:
    meta = _meta(request)
    error_body: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
        "request_id": meta["request_id"],
        "retryable": error.retryable,
    }
    if error.action:
        error_body["action"] = error.action
    if error.fields:
        error_body["fields"] = error.fields
    headers = {
        "X-Request-ID": meta["request_id"],
        "Cache-Control": "private, no-store",
    }
    if error.retry_after is not None:
        headers["Retry-After"] = str(error.retry_after)
    return JSONResponse(
        status_code=error.status_code,
        content={"error": error_body, "meta": meta},
        headers=headers,
    )


def _demo_account(db: Session) -> Account:
    """获取隔离演示账号，避免把演示数据写入真实账号。"""

    account = db.scalar(select(Account).where(Account.email_normalized == DEMO_EMAIL))
    if account is not None:
        return account
    account = Account(
        email=DEMO_EMAIL,
        email_normalized=DEMO_EMAIL,
        # 短入口账号不可登录；使用一次性随机密码，避免在代码中留下共享凭据。
        password_hash=hash_password(secrets.token_urlsafe(32)),
        registration_role="seeker",
        status="active",
        email_verified_at=utcnow(),
    )
    db.add(account)
    db.flush()
    return account


def _document_or_404(db: Session, account_id: int, public_id: str) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.public_id == public_id,
            Document.account_id == account_id,
            Document.deleted_at.is_(None),
        )
    )
    if document is None:
        raise NotFoundError()
    return document


def _version_or_404(db: Session, account_id: int, public_id: str) -> DocumentVersion:
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.public_id == public_id,
            DocumentVersion.account_id == account_id,
            DocumentVersion.deleted_at.is_(None),
        )
    )
    if version is None:
        raise NotFoundError("资料版本不存在")
    return version


def _document_view(document: Document) -> dict[str, Any]:
    return {
        "id": document.public_id,
        "document_type": document.document_type,
        "title": document.title,
        "status": document.status,
        "draft_revision": document.draft_revision,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
    }


def _version_view(version: DocumentVersion) -> dict[str, Any]:
    return {
        "id": version.public_id,
        "document_id": version.document_id,
        "version_no": version.version_no,
        "schema_version": version.schema_version,
        "confirmed_at": version.confirmed_at.isoformat(),
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时只连接 PostgreSQL；建表行为由演示环境开关控制。"""

    settings.require_postgres_url()
    if not settings.debug:
        settings.require_runtime_secrets()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        if settings.auto_create_schema:
            init_db()
    except SQLAlchemyError as exc:
        raise RuntimeError("无法连接 201 环境 PostgreSQL，请检查 DATABASE_URL") from exc
    yield


app = FastAPI(
    title="Purslyx Demo API",
    version="0.1.0",
    description="Purslyx SDD 演示用最小纵向切片：资料确认与可复核岗位匹配。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(settings.origins + settings.browser_origins)),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-CSRF-Token", "Idempotency-Key"],
)
app.include_router(api_router)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return _error(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields: list[dict[str, str]] = []
    for item in exc.errors()[:20]:
        location = [str(value) for value in item.get("loc", ()) if value != "body"]
        fields.append(
            {
                "field": ".".join(location) or "$",
                "code": str(item.get("type", "invalid")),
                "message": "字段不符合接口约定",
            }
        )
    return _error(
        request,
        DomainError("REQUEST_INVALID", "请求参数不符合接口约定", 422, fields=fields),
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """提供无构建依赖的演示页面。"""

    return FileResponse(WEB_INDEX)


@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    """健康检查明确返回 PostgreSQL 后端，便于现场验证数据库约束。"""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DomainError("DATABASE_UNAVAILABLE", "201 环境 PostgreSQL 暂不可用", 503) from exc
    return {
        "status": "ok",
        "database": {"backend": engine.url.get_backend_name(), "driver": engine.url.drivername},
        "environment": "201",
    }


@app.post("/api/v1/demo/documents", tags=["demo"], status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreateRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """提交简历或 JD 并生成待确认草稿。"""

    account = _demo_account(db)
    provider = get_model_provider()
    model_result = (
        provider.extract_resume(payload.text)
        if payload.document_type == "resume"
        else provider.extract_job(payload.text)
    )
    document = Document(
        account_id=account.id,
        document_type=payload.document_type,
        subject_type=payload.document_type,
        title=payload.title,
        source_type="text",
        status="awaiting_confirmation",
        raw_text=payload.text,
        draft_content=model_result.value,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _ok(request, _document_view(document), code=status.HTTP_201_CREATED)


@app.post("/api/v1/demo/documents/{document_id}/confirm", tags=["demo"])
def confirm_document(
    request: Request,
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """确认草稿并创建不可变版本；重复确认返回已有最新版本。"""

    account = _demo_account(db)
    document = _document_or_404(db, account.id, document_id)
    latest = db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.account_id == account.id,
            DocumentVersion.deleted_at.is_(None),
        )
        .order_by(DocumentVersion.version_no.desc())
    )
    if latest is not None:
        return _ok(request, {"document": _document_view(document), "version": _version_view(latest)})
    if not document.draft_content:
        raise DomainError("DOCUMENT_NOT_READY", "资料还没有可确认的解析草稿", 409)
    current_max = db.scalar(
        select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == document.id)
    )
    version = DocumentVersion(
        account_id=account.id,
        document_id=document.id,
        version_no=int(current_max or 0) + 1,
        content=document.draft_content,
        schema_version="document-content-v1",
    )
    document.status = "confirmed"
    db.add(version)
    db.commit()
    db.refresh(document)
    db.refresh(version)
    return _ok(request, {"document": _document_view(document), "version": _version_view(version)})


@app.get("/api/v1/demo/documents/{document_id}", tags=["demo"])
def get_document(
    request: Request,
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """读取演示资料状态，供页面刷新后继续流程。"""

    account = _demo_account(db)
    document = _document_or_404(db, account.id, document_id)
    latest = db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.account_id == account.id,
            DocumentVersion.deleted_at.is_(None),
        )
        .order_by(DocumentVersion.version_no.desc())
    )
    return _ok(
        request,
        {"document": _document_view(document), "version": _version_view(latest) if latest else None},
    )


@app.post("/api/v1/demo/matches", tags=["demo"], status_code=status.HTTP_201_CREATED)
def create_match(payload: MatchCreateRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """对两份已确认资料生成可解释的匹配报告。"""

    account = _demo_account(db)
    resume_version = _version_or_404(db, account.id, payload.resume_version_id)
    job_version = _version_or_404(db, account.id, payload.job_version_id)
    resume_document = db.get(Document, resume_version.document_id)
    job_document = db.get(Document, job_version.document_id)
    if resume_document is None or job_document is None:
        raise NotFoundError("资料不存在")
    if resume_document.document_type != "resume" or job_document.document_type != "job":
        raise DomainError("MATCH_INPUT_INVALID", "匹配必须引用一份简历和一份岗位资料", 422)

    provider = get_model_provider()
    result = provider.analyze(
        resume_version.content,
        job_version.content,
        payload.preference,
        context_type="demo",
    )
    report = result.value
    analysis = Analysis(
        account_id=account.id,
        context_type="demo",
        status="succeeded",
        resume_version_id=resume_version.id,
        job_version_id=job_version.id,
        preference_id=None,
        job_category=report.get("job_category", "general"),
        ability_score=report.get("ability_score"),
        evidence_coverage=report.get("evidence_coverage"),
        result=report,
        scoring_rule_version=report.get("scoring_rule_version", "ability-v0.1"),
        result_schema_version=report.get("result_schema_version", "analysis-result-v1"),
        prompt_version=report.get("prompt_version", "analysis-local-v1"),
        completed_at=utcnow(),
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return _ok(
        request,
        {
            "id": analysis.public_id,
            "status": analysis.status,
            "ability_score": analysis.ability_score,
            "evidence_coverage": analysis.evidence_coverage,
            "report": report,
        },
        code=status.HTTP_201_CREATED,
    )


@app.get("/api/v1/demo/analyses/{analysis_id}", tags=["demo"])
def get_analysis(
    request: Request,
    analysis_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """读取已落库报告，验证报告不是只存在于内存。"""

    account = _demo_account(db)
    analysis = db.scalar(
        select(Analysis).where(
            Analysis.public_id == analysis_id,
            Analysis.account_id == account.id,
            Analysis.deleted_at.is_(None),
        )
    )
    if analysis is None:
        raise NotFoundError("分析报告不存在")
    return _ok(
        request,
        {
            "id": analysis.public_id,
            "status": analysis.status,
            "ability_score": analysis.ability_score,
            "evidence_coverage": analysis.evidence_coverage,
            "report": analysis.result,
            "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
        },
    )
