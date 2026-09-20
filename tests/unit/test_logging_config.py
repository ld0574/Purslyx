"""容器日志必须同时保留在 stdout 和宿主机绑定目录。"""

from __future__ import annotations

import logging

from server.app.logging_config import configure_logging


def test_configure_logging_writes_rotating_file(tmp_path, monkeypatch) -> None:
    log_dir = tmp_path / "logs"
    log_file = log_dir / "worker.log"
    monkeypatch.setenv("PURSLYX_LOG_DIR", str(log_dir))
    monkeypatch.setenv("PURSLYX_LOG_FILE", "worker.log")
    monkeypatch.setenv("PURSLYX_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("PURSLYX_LOG_BACKUP_COUNT", "2")

    configure_logging("worker")
    logger = logging.getLogger("purslyx.test.logging")
    logger.error("persisted task_id=3")

    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_purslyx_file_handler", False)
        and getattr(handler, "baseFilename", "") == str(log_file.resolve())
    ]
    for handler in handlers:
        handler.flush()

    assert "persisted task_id=3" in log_file.read_text(encoding="utf-8")

    for logger_name in ("", "uvicorn", "uvicorn.access"):
        target = logging.getLogger(logger_name)
        for handler in list(target.handlers):
            if handler in handlers:
                target.removeHandler(handler)
    for handler in handlers:
        handler.close()
