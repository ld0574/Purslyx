#!/usr/bin/env python3
"""读取《本地开发环境.md》并运行 201 PostgreSQL 本地 Worker。

脚本只把连接信息注入当前进程，不打印密码；不会回退到 SQLite、202 或本地文件数据库。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_DIR / "docs/02-technical/deployment/本地开发环境.md"


def _postgres_config(path: Path) -> tuple[str, str, str, str, str]:
    if not path.is_file():
        raise SystemExit(f"找不到本地开发环境文件：{path}")
    values: dict[str, str] = {}
    in_postgres = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("postgresql:"):
            endpoint = line.split(":", 1)[1]
            parts = endpoint.split(":")
            if len(parts) != 2:
                raise SystemExit("本地开发环境文件中的 PostgreSQL 地址格式无效")
            values["host"], values["port"] = parts
            in_postgres = True
            continue
        if line.startswith("redis:"):
            in_postgres = False
            continue
        if in_postgres and ":" in line:
            key, value = line.split(":", 1)
            if key in {"用户名", "数据库", "密码"}:
                values[key] = value.strip()
    missing = [key for key in ("host", "port", "用户名", "数据库", "密码") if not values.get(key)]
    if missing:
        raise SystemExit("本地开发环境文件中缺少完整 PostgreSQL 连接信息")
    if values["host"] != "10.10.10.201":
        raise SystemExit("本地开发环境文件中的 PostgreSQL 必须是 201 环境")
    return values["host"], values["port"], values["用户名"], values["数据库"], values["密码"]


def _configure_environment() -> None:
    host, port, user, database, password = _postgres_config(ENV_FILE)
    expected_url = f"postgresql+psycopg://{user}@{host}:{port}/{database}"
    if os.getenv("DATABASE_URL", expected_url) != expected_url:
        raise SystemExit("DATABASE_URL 必须固定为本地开发环境文件中的 201 PostgreSQL")
    os.environ["DATABASE_URL"] = expected_url
    os.environ["PGPASSWORD"] = password
    os.environ.setdefault("PURSLYX_DEBUG", "true")
    os.environ.setdefault("PURSLYX_AUTO_CREATE_SCHEMA", "false")
    # Worker 自身必须执行任务，即使启动它的 shell 同时把 HTTP 服务设为 worker 模式。
    os.environ["PURSLYX_EXECUTION_MODE"] = "inline"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purslyx 201 PostgreSQL local worker")
    parser.add_argument("--once", action="store_true", help="只处理一批事件后退出")
    parser.add_argument("--owner", default=f"local-worker-{os.getpid()}")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--lease-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    _configure_environment()
    args = _args()
    if not 1 <= args.batch_size <= 100:
        raise SystemExit("--batch-size 必须在 1 到 100 之间")
    if args.lease_seconds < 1 or not 0 < args.poll_seconds <= 30:
        raise SystemExit("租约和轮询参数超出允许范围")

    from server.app.db import seed_db
    from server.app.worker import TaskWorker

    # Worker 只处理迁移完成后的业务数据，不在运行时隐式修改数据库结构。
    seed_db()
    worker = TaskWorker(owner=args.owner, batch_size=args.batch_size, lease_seconds=args.lease_seconds)
    if args.once:
        processed = worker.run_once()
        print(json.dumps({"environment": "201", "database": "postgresql", "processed": processed}, ensure_ascii=False))
        return
    while True:
        worker.run_once()
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
