"""本地演示环境的私有文件存储边界。

文件正文不进入任务、日志或错误响应。该模块只负责容量检查、目录内路径校验和原子
落盘；生产部署可以把同一组端口替换成对象存储实现。
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .errors import DomainError
from .models import StoredFile


def ensure_storage_capacity(db: Session, incoming_bytes: int) -> None:
    """在写文件前检查已登记的私有原件容量，避免先落盘后发现超限。"""

    if incoming_bytes < 0:
        raise DomainError("STORAGE_CAPACITY_LIMIT", "文件大小无效", 503)
    used = int(
        db.scalar(
            select(func.coalesce(func.sum(StoredFile.byte_size), 0)).where(
                StoredFile.deleted_at.is_(None),
                StoredFile.status == "available",
            )
        )
        or 0
    )
    if used + incoming_bytes > settings.storage_limit_bytes:
        raise DomainError("STORAGE_CAPACITY_LIMIT", "本地私有存储空间不足，请稍后清理后重试", 503, "retry")


def atomic_write_bytes(directory: Path, payload: bytes, suffix: str) -> Path:
    """在目标目录内使用临时文件＋fsync＋replace 原子保存字节。"""

    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{secrets.token_hex(16)}{suffix}"
    temporary = directory / f".{target.name}.uploading"
    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return target
    except Exception:
        if file_descriptor is not None:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def private_path(path_value: str | Path, root: Path | None = None) -> Path:
    """解析并限制一个内部存储键只能位于指定私有目录。"""

    root_path = (root or settings.file_dir).resolve()
    candidate = Path(path_value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        # 兼容早期演示库把 ``data/files/x`` 作为相对路径写入字段；当前逻辑键则
        # 是相对于 data 根目录的 ``files/x``。两种解释都必须通过同一个根目录约束。
        working_directory_candidate = candidate.resolve()
        try:
            working_directory_candidate.relative_to(root_path)
        except ValueError:
            resolved = (root_path / candidate).resolve()
        else:
            resolved = working_directory_candidate
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise DomainError("STORAGE_PATH_INVALID", "私有文件路径无效", 500) from exc
    return resolved


def storage_key(path_value: str | Path, root: Path | None = None) -> str:
    """把私有文件转换为不含主机绝对路径的逻辑键。"""

    root_path = (root or settings.data_dir).resolve()
    resolved = private_path(path_value, root_path)
    return resolved.relative_to(root_path).as_posix()


def safe_download_name(original_filename: str | None, fallback: str) -> str:
    """清理下载文件名，不允许路径分隔符、控制字符或空文件名。"""

    value = (original_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip().strip(".")
    return value[:120] or fallback
