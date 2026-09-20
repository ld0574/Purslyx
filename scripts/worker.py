#!/usr/bin/env python3
"""运行容器化部署使用的 Purslyx PostgreSQL Worker。

与 ``worker_201.py`` 不同，本入口不读取本地开发环境文件，数据库和其他运行配置
全部通过环境变量注入，适合 Docker、systemd 或其他进程管理器。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


LOGGER = logging.getLogger("purslyx.worker")


def _configure_logging() -> None:
    level_name = os.getenv("PURSLYX_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purslyx PostgreSQL Worker")
    parser.add_argument("--once", action="store_true", help="只处理一批事件后退出")
    parser.add_argument("--owner", default=f"container-worker-{os.getpid()}")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--lease-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    _configure_logging()
    # Worker 进程自身必须执行任务，即使 API 进程配置为 worker 模式。
    os.environ["PURSLYX_EXECUTION_MODE"] = "inline"

    from server.app.config import settings
    from server.app.db import seed_db
    from server.app.worker import TaskWorker

    settings.require_postgres_url()
    args = _args()
    if not 1 <= args.batch_size <= 100:
        raise SystemExit("--batch-size 必须在 1 到 100 之间")
    if args.lease_seconds < 1 or not 0 < args.poll_seconds <= 30:
        raise SystemExit("租约和轮询参数超出允许范围")

    # 只初始化目录和幂等种子，不隐式创建数据库结构；结构由发布流程先执行 Alembic。
    seed_db()
    worker = TaskWorker(owner=args.owner, batch_size=args.batch_size, lease_seconds=args.lease_seconds)
    LOGGER.info(
        "worker started owner=%s batch_size=%s lease_seconds=%s poll_seconds=%s",
        args.owner,
        args.batch_size,
        args.lease_seconds,
        args.poll_seconds,
    )
    if args.once:
        processed = worker.run_once()
        LOGGER.info("worker stopped after one batch processed=%s", processed)
        print(json.dumps({"database": "postgresql", "processed": processed}, ensure_ascii=False))
        return
    while True:
        try:
            processed = worker.run_once()
            if processed:
                LOGGER.info("worker processed events=%s", processed)
        except Exception:
            # 数据库短暂断开时保持 Worker 进程存活，下一轮继续恢复 outbox；异常详情会进入
            # docker logs，且不打印简历、JD 或模型输入正文。
            LOGGER.exception("worker polling failed; will retry")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
