"""数据库连接与初始化。"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


# 启动即拒绝非 PostgreSQL URL；不保留 SQLite 的兼容分支，避免测试配置误上生产。
engine = create_engine(settings.require_postgres_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：为一个请求提供独立会话。"""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """脚本和后台任务使用的事务上下文。"""

    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """兼容旧开发流程的幂等初始化；正式启动应先执行 Alembic。"""

    from . import models  # noqa: F401  # 确保所有模型已注册
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.file_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _upgrade_legacy_schema()
    seed_db()


def seed_db() -> None:
    """在已经迁移完成的数据库中写入固定权限和本地开发账号。"""

    from .seed import seed_permissions

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.file_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    with session_scope() as db:
        seed_permissions(db)


def _upgrade_legacy_schema() -> None:
    """把早期开发库平滑升级到当前模型，不删除已经产生的业务记录。

    第一版去投递表曾保存过原始 URL；当前设计只保存 URL 摘要。为了让 201 上已经
    初始化过的库仍可直接启动，这里只补新列、回填摘要并放宽旧列约束，后续正式环境
    再由 Alembic 迁移负责清理旧列。
    """

    with engine.begin() as connection:
        inspector = inspect(connection)
        # 201 上可能已经存在早期开发表；create_all 不会为已有表补列，所以把
        # 本轮契约新增的幂等字段以可重复执行的 ALTER TABLE 平滑补齐。
        idempotency_columns = {
            "job_pool_items": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "document_versions": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "job_preferences": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "job_preference_versions": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "resume_facts": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "resume_fact_versions": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "async_tasks": ("request_hash", "VARCHAR(64)"),
            "resume_segment_decisions": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "resume_variants": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "resume_variant_versions": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "product_feedback": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "browser_job_drafts": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "interview_answers": ("request_hash", "VARCHAR(64)"),
            "log_exports": ("idempotency_key", "VARCHAR(128)", "request_hash", "VARCHAR(64)"),
            "admin_audit_events": ("idempotency_key", "VARCHAR(128)"),
        }
        for table_name, column_values in idempotency_columns.items():
            if table_name not in inspector.get_table_names():
                continue
            columns = {item["name"] for item in inspector.get_columns(table_name)}
            for index in range(0, len(column_values), 2):
                column_name, column_type = column_values[index : index + 2]
                if column_name not in columns:
                    connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'))
        if "job_pool_items" in inspector.get_table_names():
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uk_job_pool_account_idempotency "
                    "ON job_pool_items(account_id, idempotency_key) "
                    "WHERE idempotency_key IS NOT NULL"
                )
            )
        additive_columns = {
            # 201 上的早期开发库可能已经有这些表，但还没有当前审计字段。
            "accounts": (("revision", "INTEGER DEFAULT 1"),),
            "product_feedback": (
                ("revision", "INTEGER DEFAULT 1"),
                ("reviewed_by_account_id", "INTEGER"),
                ("reviewed_at", "TIMESTAMPTZ"),
            ),
            "analysis_reports": (("preference_version_id", "INTEGER"),),
            "task_outbox": (("claimed_by", "VARCHAR(120)"), ("claimed_at", "TIMESTAMPTZ")),
        }
        for table_name, column_values in additive_columns.items():
            if table_name not in inspector.get_table_names():
                continue
            columns = {item["name"] for item in inspector.get_columns(table_name)}
            for column_name, column_type in column_values:
                if column_name not in columns:
                    connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'))
        if "resume_variant_versions" in inspector.get_table_names():
            columns = {item["name"] for item in inspector.get_columns("resume_variant_versions")}
            if "rewrite_id" not in columns:
                connection.execute(text("ALTER TABLE resume_variant_versions ADD COLUMN rewrite_id INTEGER"))
            if "template_version" not in columns:
                connection.execute(text("ALTER TABLE resume_variant_versions ADD COLUMN template_version VARCHAR(80) DEFAULT 'resume-template-v1'"))
        if "documents" in inspector.get_table_names():
            document_columns = {item["name"] for item in inspector.get_columns("documents")}
            if "idempotency_key" not in document_columns:
                connection.execute(text("ALTER TABLE documents ADD COLUMN idempotency_key VARCHAR(128)"))
            if "request_hash" not in document_columns:
                connection.execute(text("ALTER TABLE documents ADD COLUMN request_hash VARCHAR(64)"))
        if "apply_click_events" in inspector.get_table_names():
            columns = {item["name"] for item in inspector.get_columns("apply_click_events")}
            if "source_url_hash" not in columns:
                connection.execute(text("ALTER TABLE apply_click_events ADD COLUMN source_url_hash VARCHAR(64)"))
                if "target_url" in columns:
                    connection.execute(
                        text(
                            "UPDATE apply_click_events "
                            "SET source_url_hash = md5(COALESCE(target_url, '')) "
                            "WHERE source_url_hash IS NULL"
                        )
                    )
                else:
                    connection.execute(
                        text("UPDATE apply_click_events SET source_url_hash = md5(public_id) WHERE source_url_hash IS NULL")
                    )
                connection.execute(text("ALTER TABLE apply_click_events ALTER COLUMN source_url_hash SET NOT NULL"))
            if "metric_date" not in columns:
                connection.execute(text("ALTER TABLE apply_click_events ADD COLUMN metric_date VARCHAR(10)"))
                connection.execute(
                    text(
                        "UPDATE apply_click_events SET metric_date = "
                        "to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD') "
                        "WHERE metric_date IS NULL"
                    )
                )
                connection.execute(text("ALTER TABLE apply_click_events ALTER COLUMN metric_date SET NOT NULL"))
            if "target_url" in columns:
                connection.execute(text("ALTER TABLE apply_click_events ALTER COLUMN target_url DROP NOT NULL"))
        numeric_columns = {
            "model_call_attempts": ("cost_usd",),
            "site_budget_buckets": ("budget_usd", "reserved_usd", "settled_usd", "unknown_usd"),
            "budget_reservations": ("upper_bound_usd", "actual_cost_usd"),
        }
        for table_name, column_names in numeric_columns.items():
            if table_name not in inspector.get_table_names():
                continue
            column_info = {item["name"]: item for item in inspector.get_columns(table_name)}
            for column_name in column_names:
                if column_name in column_info and "numeric" not in str(column_info[column_name]["type"]).lower():
                    connection.execute(
                        text(
                            f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" '
                            "TYPE NUMERIC(20,8) USING "
                            f'"{column_name}"::numeric'
                        )
                    )
        if "budget_reservations" in inspector.get_table_names():
            columns = {item["name"] for item in inspector.get_columns("budget_reservations")}
            if "call_key" not in columns:
                connection.execute(text("ALTER TABLE budget_reservations ADD COLUMN call_key VARCHAR(160)"))
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uk_budget_reservation_call_key ON budget_reservations(call_key) WHERE call_key IS NOT NULL"))
