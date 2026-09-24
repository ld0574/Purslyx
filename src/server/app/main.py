"""Purslyx Web 与 API 服务。"""

from __future__ import annotations

import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .api import router as api_router
from .config import settings
from .db import engine, init_db
from .errors import DomainError
from .logging_config import configure_logging

configure_logging("api")

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
WEB_DIST_ROOT = WEB_ROOT / "dist"
WEB_INDEX = WEB_DIST_ROOT / "index.html"
WEB_ASSET_ROOT = WEB_DIST_ROOT / "assets"
WEB_FAVICON = WEB_DIST_ROOT / "favicon.svg"
WEB_LOGO = WEB_DIST_ROOT / "purslyx-logo.png"
USERSCRIPT_FILE = Path(__file__).resolve().parents[2] / "userscript" / "purslyx-job-capture.user.js"


def utcnow() -> datetime:
    """返回带时区的 UTC 时间，避免把本机时区写入业务记录。"""

    return datetime.now(timezone.utc)


def _meta(request: Request) -> dict[str, str]:
    """为错误响应生成轻量元信息，方便定位一次请求。"""

    request_id = getattr(request.state, "request_id", "") or request.headers.get("X-Request-ID", "").strip()
    if not request_id or len(request_id) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", request_id):
        request_id = secrets.token_hex(12)
    request.state.request_id = request_id
    return {"request_id": request_id, "server_time": utcnow().isoformat()}


def _error(request: Request, error: DomainError) -> JSONResponse:
    meta = _meta(request)
    logging.getLogger("purslyx.error").log(
        logging.ERROR if error.status_code >= 500 else logging.WARNING,
        "request error request_id=%s method=%s path=%s status=%s error_code=%s cause_type=%s",
        meta["request_id"],
        request.method,
        request.url.path,
        error.status_code,
        error.code,
        type(error.__cause__).__name__ if error.__cause__ else "none",
    )
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
    """启动时只连接已允许的 PostgreSQL；建表行为由环境开关控制。"""

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
        raise RuntimeError("无法连接 PostgreSQL，请检查 DATABASE_URL 和数据库网络访问") from exc
    yield


app = FastAPI(
    title="Purslyx API",
    version="0.1.0",
    description="Purslyx 求职、招聘与授权管理服务。",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Any) -> Response:
    """记录请求关联 ID、状态和耗时，但不记录 Cookie、Authorization 或请求正文。"""

    request_id = request.headers.get("X-Request-ID", "").strip()
    if not request_id or len(request_id) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", request_id):
        request_id = secrets.token_hex(12)
    request.state.request_id = request_id
    started = time.perf_counter()
    request_logger = logging.getLogger("purslyx.request")
    try:
        response = await call_next(request)
    except Exception as exc:
        if request.url.path.startswith("/api/v1/documents"):
            # 解析器或供应商异常可能在 traceback 中包含上传正文，不能写入持久日志。
            request_logger.error(
                "request failed request_id=%s method=%s path=%s duration_ms=%.1f error_type=%s cause_type=%s",
                request_id, request.method, request.url.path, (time.perf_counter() - started) * 1000,
                type(exc).__name__, type(exc.__cause__).__name__ if exc.__cause__ else "none",
            )
        else:
            request_logger.exception(
                "request failed request_id=%s method=%s path=%s duration_ms=%.1f",
                request_id, request.method, request.url.path, (time.perf_counter() - started) * 1000,
            )
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers.setdefault("X-Request-ID", request_id)
    request_logger.log(
        logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400 else logging.INFO,
        "request completed request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(settings.origins + settings.browser_origins)),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-CSRF-Token", "Idempotency-Key"],
)
app.include_router(api_router)
# Vite 生产构建的带哈希资源统一从 /assets 提供；目录缺失时页面入口会返回明确的构建提示。
app.mount("/assets", StaticFiles(directory=WEB_ASSET_ROOT, check_dir=False), name="web-assets")


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return _error(request, exc)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, _: IntegrityError) -> JSONResponse:
    """数据库唯一约束是并发写入的最后防线，不向客户端泄露 SQL 或字段值。"""

    return _error(
        request,
        DomainError("WRITE_CONFLICT", "数据已被另一个请求更新，请刷新后重试", 409, "refresh"),
    )


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
    meta = _meta(request)
    logging.getLogger("purslyx.validation").warning(
        "request validation failed request_id=%s method=%s path=%s fields=%s",
        meta["request_id"],
        request.method,
        request.url.path,
        ",".join(f"{item['field']}:{item['code']}" for item in fields) or "none",
    )
    return _error(
        request,
        DomainError("REQUEST_INVALID", "请求参数不符合接口约定", 422, fields=fields),
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """公共首页与工作台共用 Vue 应用入口。"""

    return _web_index()


@app.get("/guide", include_in_schema=False)
def guide_page() -> FileResponse:
    """使用说明是 Vue 公共路由，直接访问或刷新时也必须返回 SPA 入口。"""

    return _web_index()


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    """提供 Vite public 目录复制出的站点图标。"""

    if not WEB_FAVICON.is_file():
        raise DomainError("WEB_BUILD_MISSING", "前端站点图标尚未构建", 503)
    return FileResponse(WEB_FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/purslyx-logo.png", include_in_schema=False)
def purslyx_logo() -> FileResponse:
    """提供首页使用的品牌 Logo。"""

    if not WEB_LOGO.is_file():
        raise DomainError("WEB_BUILD_MISSING", "前端品牌 Logo 尚未构建", 503)
    return FileResponse(WEB_LOGO, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/purslyx-job-capture.user.js", include_in_schema=False)
def userscript() -> FileResponse:
    """提供篡改猴一键安装和自动更新脚本。"""

    if not USERSCRIPT_FILE.is_file():
        raise DomainError("USERSCRIPT_MISSING", "浏览器脚本尚未发布", 503)
    return FileResponse(
        USERSCRIPT_FILE,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _web_index() -> FileResponse:
    """返回 SPA 入口；开发者忘记构建前端时给出可执行的修复说明。"""

    if not WEB_INDEX.is_file():
        raise DomainError("WEB_BUILD_MISSING", "前端尚未构建，请在 src/web 执行 npm ci && npm run build", 503)
    return FileResponse(WEB_INDEX, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/app/{path:path}", include_in_schema=False)
def app_page(path: str) -> FileResponse:
    """Vue Router 的认证、业务和管理路由都回落到同一生产入口。"""

    return _web_index()


@app.get("/job-pool/items", include_in_schema=False)
def job_pool_page() -> FileResponse:
    """让浏览器脚本返回的确认链接直接落到匹配池独立页面。"""

    return _web_index()


@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    """健康检查明确返回 PostgreSQL 后端和部署环境，便于验证运行事实。"""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DomainError("DATABASE_UNAVAILABLE", "PostgreSQL 暂不可用", 503) from exc
    return {
        "status": "ok",
        "database": {"backend": engine.url.get_backend_name(), "driver": engine.url.drivername},
        "environment": settings.environment_name,
    }
