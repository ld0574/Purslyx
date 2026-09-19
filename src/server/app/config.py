"""应用配置。

数据库连接只允许由部署环境注入 PostgreSQL URL。这里不提供 SQLite、本地文件数据库或
任何真实凭据的默认值；允许的数据库主机由 PURSLYX_ALLOWED_DATABASE_HOSTS 控制。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("PURSLYX_APP_NAME", "Purslyx"))
    environment_name: str = field(default_factory=lambda: os.getenv("PURSLYX_ENVIRONMENT", "201").strip())
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "").strip())
    allowed_database_hosts: str = field(
        default_factory=lambda: os.getenv("PURSLYX_ALLOWED_DATABASE_HOSTS", "10.10.10.201").strip()
    )
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "").strip())
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("PURSLYX_DATA_DIR", "data")))
    debug: bool = field(default_factory=lambda: _bool("PURSLYX_DEBUG", False))
    auto_create_schema: bool = field(
        default_factory=lambda: _bool("PURSLYX_AUTO_CREATE_SCHEMA", True)
    )
    # 默认先不要求邮箱所有权验证；生产需要时设置为 true 即恢复验证流程。
    require_email_verification: bool = field(
        default_factory=lambda: _bool("PURSLYX_REQUIRE_EMAIL_VERIFICATION", False)
    )
    session_days: int = field(default_factory=lambda: int(os.getenv("PURSLYX_SESSION_DAYS", "7")))
    verification_token_hours: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_VERIFICATION_TOKEN_HOURS", "24"))
    )
    password_reset_minutes: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_PASSWORD_RESET_MINUTES", "30"))
    )
    account_recovery_minutes: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_ACCOUNT_RECOVERY_MINUTES", "30"))
    )
    browser_auth_code_minutes: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_BROWSER_AUTH_CODE_MINUTES", "1"))
    )
    browser_session_hours: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_BROWSER_SESSION_HOURS", "4"))
    )
    site_budget_usd: str = field(default_factory=lambda: os.getenv("PURSLYX_SITE_BUDGET_USD", "30.00"))
    model_concurrency_limit: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_MODEL_CONCURRENCY_LIMIT", "5"))
    )
    account_running_task_limit: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_ACCOUNT_RUNNING_TASK_LIMIT", "1"))
    )
    account_pending_task_limit: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_ACCOUNT_PENDING_TASK_LIMIT", "3"))
    )
    storage_limit_bytes: int = field(
        default_factory=lambda: int(os.getenv("PURSLYX_STORAGE_LIMIT_BYTES", str(2 * 1024 * 1024 * 1024)))
    )
    cookie_secure: bool = field(default_factory=lambda: _bool("PURSLYX_COOKIE_SECURE", False))
    token_secret: str = field(default_factory=lambda: os.getenv("PURSLYX_TOKEN_SECRET", ""))
    product_origin: str = field(
        default_factory=lambda: os.getenv("PURSLYX_PRODUCT_ORIGIN", "http://127.0.0.1:8001")
    )
    allowed_origins: str = field(
        default_factory=lambda: os.getenv(
            "PURSLYX_ALLOWED_ORIGINS",
            "http://127.0.0.1:8001,http://localhost:8001",
        )
    )
    allowed_browser_origins: str = field(
        default_factory=lambda: os.getenv(
            "PURSLYX_ALLOWED_BROWSER_ORIGINS",
            "https://www.zhipin.com,https://zhipin.com,https://www.liepin.com,https://liepin.com",
        )
    )
    model_provider: str = field(default_factory=lambda: os.getenv("PURSLYX_MODEL_PROVIDER", "local"))
    model_name: str = field(default_factory=lambda: os.getenv("PURSLYX_MODEL", "gpt-5.6-luna"))
    execution_mode: str = field(default_factory=lambda: os.getenv("PURSLYX_EXECUTION_MODE", "inline").strip().lower())
    model_input_usd_per_million: str = field(default_factory=lambda: os.getenv("PURSLYX_MODEL_INPUT_USD_PER_MILLION", ""))
    model_cached_input_usd_per_million: str = field(default_factory=lambda: os.getenv("PURSLYX_MODEL_CACHED_INPUT_USD_PER_MILLION", ""))
    model_output_usd_per_million: str = field(default_factory=lambda: os.getenv("PURSLYX_MODEL_OUTPUT_USD_PER_MILLION", ""))
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openai_base_url: str | None = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL"))
    smtp_host: str | None = field(default_factory=lambda: os.getenv("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_username: str | None = field(default_factory=lambda: os.getenv("SMTP_USERNAME"))
    smtp_password: str | None = field(default_factory=lambda: os.getenv("SMTP_PASSWORD"))
    smtp_from: str | None = field(default_factory=lambda: os.getenv("SMTP_FROM"))
    smtp_starttls: bool = field(default_factory=lambda: _bool("SMTP_STARTTLS", True))
    admin_email: str = field(default_factory=lambda: os.getenv("PURSLYX_ADMIN_EMAIL", ""))
    admin_password: str = field(default_factory=lambda: os.getenv("PURSLYX_ADMIN_PASSWORD", ""))

    def require_postgres_url(self) -> str:
        """校验数据库连接，应用启动前必须通过这条检查。"""

        value = self.database_url.strip()
        scheme = value.split(":", 1)[0].lower() if ":" in value else ""
        if not value:
            raise RuntimeError(
                "未配置 DATABASE_URL；Purslyx 只能连接已允许的 PostgreSQL，"
                "请通过部署环境注入连接串。"
            )
        if scheme.startswith("sqlite"):
            raise RuntimeError("禁止使用 SQLite；请将 DATABASE_URL 配置为 PostgreSQL URL。")
        if scheme not in {"postgres", "postgresql"} and not scheme.startswith("postgresql+"):
            raise RuntimeError("DATABASE_URL 必须是 PostgreSQL URL，不能使用本地文件数据库。")
        hostname = urlparse(value).hostname
        allowed_hosts = self.database_hosts
        if allowed_hosts and hostname not in allowed_hosts:
            allowed = ", ".join(allowed_hosts)
            raise RuntimeError(f"DATABASE_URL 必须指向允许的 PostgreSQL 主机（当前允许：{allowed}）。")
        return value

    @property
    def database_hosts(self) -> list[str]:
        """返回允许连接的 PostgreSQL 主机列表。"""

        return [item.strip() for item in self.allowed_database_hosts.split(",") if item.strip()]

    def require_runtime_secrets(self) -> None:
        """校验线上必需的签名配置；单元测试可只调用数据库校验。"""

        if not self.token_secret or len(self.token_secret) < 32:
            raise RuntimeError("PURSLYX_TOKEN_SECRET 至少需要 32 个字符。")

    def require_execution_mode(self) -> None:
        """只允许已实现的本地执行模式，避免拼写错误导致任务永久排队。"""

        if self.execution_mode not in {"inline", "worker"}:
            raise RuntimeError("PURSLYX_EXECUTION_MODE 只能是 inline 或 worker。")

    @property
    def origins(self) -> list[str]:
        """返回经过清理的 CORS 来源列表。"""

        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def browser_origins(self) -> list[str]:
        """返回允许浏览器脚本发起跨源请求的固定来源。"""

        return [item.strip() for item in self.allowed_browser_origins.split(",") if item.strip()]

    @property
    def file_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def outbox_file(self) -> Path:
        return self.data_dir / "outbox.log"


settings = Settings()
