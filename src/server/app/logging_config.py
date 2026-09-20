"""统一配置容器服务日志。

日志同时写到 stdout 和可轮转的文件。stdout 方便 Docker 运行时采集，文件则通过 Compose
的 bind mount 保存在宿主机，避免滚动发布删除旧容器时丢失排障记录。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_FILE_HANDLER_MARKER = "_purslyx_file_handler"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 10


def _positive_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _file_handler_for(path: Path) -> logging.Handler | None:
    target = str(path.resolve())
    for handler in logging.getLogger().handlers:
        if getattr(handler, _FILE_HANDLER_MARKER, False) and getattr(handler, "baseFilename", "") == target:
            return handler
    return None


def _attach_once(logger: logging.Logger, handler: logging.Handler) -> None:
    if not any(item is handler for item in logger.handlers):
        logger.addHandler(handler)


def configure_logging(service: str) -> None:
    """配置一个服务的 stdout 和宿主机文件日志。"""

    level_name = os.getenv("PURSLYX_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        stream=sys.stdout,
    )
    root = logging.getLogger()
    root.setLevel(level)

    log_dir = os.getenv("PURSLYX_LOG_DIR", "").strip()
    if not log_dir:
        return

    file_name = os.getenv("PURSLYX_LOG_FILE", f"{service}.log").strip() or f"{service}.log"
    if Path(file_name).name != file_name:
        raise RuntimeError("PURSLYX_LOG_FILE 只能是文件名，不能包含目录")

    path = Path(log_dir) / file_name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = _file_handler_for(path)
        if handler is None:
            handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=_positive_int("PURSLYX_LOG_MAX_BYTES", _DEFAULT_MAX_BYTES),
                backupCount=_positive_int("PURSLYX_LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT),
                encoding="utf-8",
            )
            setattr(handler, _FILE_HANDLER_MARKER, True)
            handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
            root.addHandler(handler)
    except OSError as exc:
        raise RuntimeError(f"无法写入日志目录 {path.parent}，请检查宿主机挂载目录权限") from exc

    # Uvicorn 的 error/access logger 默认禁止向 root 传播；显式挂载文件 handler，确保
    # 请求日志、启动错误和未处理异常也落到宿主机文件。
    _attach_once(logging.getLogger("uvicorn"), handler)
    _attach_once(logging.getLogger("uvicorn.access"), handler)
