"""Purslyx Web 与 API 服务。"""

from __future__ import annotations

import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .api import router as api_router
from .config import settings
from .db import engine, init_db
from .errors import DomainError, NotFoundError

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
WEB_PAGE_ROOT = WEB_ROOT / "pages"
WEB_ASSET_ROOT = WEB_ROOT / "assets"

# 正式工作台按页面职责拆分入口；页面内部仍复用同一套会话、API 和视觉组件。
WEB_PAGES = {
    "home": "home.html",
    "login": "login.html",
    "register": "register.html",
    "seeker-dashboard": "seeker-dashboard.html",
    "seeker-resume": "seeker-resume.html",
    "seeker-pool": "seeker-pool.html",
    "seeker-report": "seeker-report.html",
    "seeker-rewrite": "seeker-rewrite.html",
    "seeker-variants": "seeker-variants.html",
    "seeker-interview": "seeker-interview.html",
    "seeker-tasks": "seeker-tasks.html",
    "seeker-usage": "seeker-usage.html",
    "seeker-stats": "seeker-stats.html",
    "recruiter-dashboard": "recruiter-dashboard.html",
    "recruiter-materials": "recruiter-materials.html",
    "recruiter-report": "recruiter-report.html",
    "recruiter-tasks": "recruiter-tasks.html",
    "recruiter-usage": "recruiter-usage.html",
    "recruiter-stats": "recruiter-stats.html",
    "admin-metrics": "admin-metrics.html",
    "admin-users": "admin-users.html",
    "admin-roles": "admin-roles.html",
    "admin-usage": "admin-usage.html",
    "admin-logs": "admin-logs.html",
}


def utcnow() -> datetime:
    """返回带时区的 UTC 时间，避免把本机时区写入业务记录。"""

    return datetime.now(timezone.utc)


def _meta(request: Request) -> dict[str, str]:
    """为错误响应生成轻量元信息，方便定位一次请求。"""

    request_id = request.headers.get("X-Request-ID", "").strip()
    if not request_id or len(request_id) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", request_id):
        request_id = secrets.token_hex(12)
    request.state.request_id = request_id
    return {"request_id": request_id, "server_time": utcnow().isoformat()}


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时只连接 201 PostgreSQL；建表行为由开发环境开关控制。"""

    settings.require_postgres_url()
    settings.require_execution_mode()
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
    title="Purslyx API",
    version="0.1.0",
    description="Purslyx 求职、招聘与授权管理服务。",
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
    """提供独立的公共首页；正式业务从登录页和各模块页面进入。"""

    return FileResponse(WEB_PAGE_ROOT / WEB_PAGES["home"], headers={"Cache-Control": "no-store"})


@app.get("/app/{page_name}", include_in_schema=False)
def auth_page(page_name: str = PathParam(min_length=1, max_length=32)) -> FileResponse:
    """提供登录和注册这两个独立的认证入口。"""

    if page_name not in {"login", "register"}:
        raise NotFoundError("页面不存在")
    return FileResponse(WEB_PAGE_ROOT / WEB_PAGES[page_name], headers={"Cache-Control": "no-store"})


@app.get("/app/{role}/{page_name}", include_in_schema=False)
def workbench_page(
    role: str = PathParam(min_length=1, max_length=32),
    page_name: str = PathParam(min_length=1, max_length=32),
) -> FileResponse:
    """提供求职、招聘和管理端的独立模块页面入口。"""

    page_key = f"{role}-{page_name}"
    if page_key not in WEB_PAGES:
        raise NotFoundError("页面不存在")
    return FileResponse(WEB_PAGE_ROOT / WEB_PAGES[page_key], headers={"Cache-Control": "no-store"})


@app.get("/assets/styles.css", include_in_schema=False)
def web_stylesheet() -> FileResponse:
    """返回所有页面共享的视觉样式。"""

    return FileResponse(WEB_ASSET_ROOT / "styles.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/assets/workbench.js", include_in_schema=False)
def web_workbench_script() -> FileResponse:
    """返回所有页面共享的 API 客户端和交互逻辑。"""

    return FileResponse(WEB_ASSET_ROOT / "workbench.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/job-pool/items", include_in_schema=False)
def job_pool_page() -> FileResponse:
    """让浏览器脚本返回的确认链接直接落到匹配池独立页面。"""

    return FileResponse(WEB_PAGE_ROOT / WEB_PAGES["seeker-pool"], headers={"Cache-Control": "no-store"})


@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    """健康检查明确返回 PostgreSQL 后端，便于验证数据库约束。"""

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
