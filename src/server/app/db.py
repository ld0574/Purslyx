"""数据库连接与初始化。"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
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
    """创建本地开发所需表并种入固定权限目录。"""

    from . import models  # noqa: F401  # 确保所有模型已注册
    from .seed import seed_permissions

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.file_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    with session_scope() as db:
        seed_permissions(db)
