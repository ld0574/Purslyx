#!/usr/bin/env python3
"""读取《本地开发环境.md》并执行 201 PostgreSQL Alembic 迁移。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_DIR / "docs/02-technical/deployment/本地开发环境.md"


def configure_environment() -> None:
    if not ENV_FILE.is_file():
        raise SystemExit(f"找不到本地开发环境文件：{ENV_FILE}")
    values: dict[str, str] = {}
    in_postgres = False
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("postgresql:"):
            host, port = line.split(":", 1)[1].split(":")
            values.update({"host": host, "port": port})
            in_postgres = True
        elif line.startswith("redis:"):
            in_postgres = False
        elif in_postgres and ":" in line:
            key, value = line.split(":", 1)
            if key in {"用户名", "数据库", "密码"}:
                values[key] = value.strip()
    required = ("host", "port", "用户名", "数据库", "密码")
    if any(not values.get(key) for key in required) or values.get("host") != "10.10.10.201":
        raise SystemExit("本地开发环境文件中的 PostgreSQL 必须是完整的 201 配置")
    url = f"postgresql+psycopg://{values['用户名']}@{values['host']}:{values['port']}/{values['数据库']}"
    if os.getenv("DATABASE_URL", url) != url:
        raise SystemExit("DATABASE_URL 必须固定为本地开发环境文件中的 201 PostgreSQL")
    os.environ["DATABASE_URL"] = url
    os.environ["PGPASSWORD"] = values["密码"]


def main() -> None:
    parser = argparse.ArgumentParser(description="只针对《本地开发环境.md》的 201 PostgreSQL 执行迁移检查")
    parser.add_argument(
        "action",
        nargs="?",
        choices=("upgrade", "current", "check", "verify"),
        default="upgrade",
        help="默认 upgrade；verify 会依次执行 current 和 check",
    )
    args = parser.parse_args()
    configure_environment()
    commands = {
        "upgrade": (("upgrade", "head"),),
        "current": (("current",),),
        "check": (("check",),),
        "verify": (("current",), ("check",)),
    }[args.action]
    for command in commands:
        result = subprocess.run([sys.executable, "-m", "alembic", *command], cwd=PROJECT_DIR, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
