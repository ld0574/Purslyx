#!/usr/bin/env python3
"""在随机隔离 Schema 演练新版面试迁移与 Worker；不触碰已有业务表。"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from migrate_201 import configure_environment
from sqlalchemy import create_engine, text

PROJECT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    configure_environment()
    admin_engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 5})
    schemas = [f"coaching_test_{uuid.uuid4().hex}" for _ in range(2)]
    created = []
    try:
        for schema in schemas:
            with admin_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created.append(schema)
        run_env = {
            **os.environ,
            "PURSLYX_MODEL_PROVIDER": "local",
            "PURSLYX_AUTO_CREATE_SCHEMA": "false",
            "PURSLYX_TEST_POSTGRES": "1",
            "PYTHONPATH": str(PROJECT_DIR / "src"),
        }
        # 分开进程，保证每个连接只使用指定 Schema，没有缓存连接指向 public。
        run_env["PGOPTIONS"] = f"-c search_path={schemas[0]}"
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_DIR,
            env=run_env,
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "check"], cwd=PROJECT_DIR, env=run_env, check=True
        )
        print("PASS: fresh PostgreSQL migration chain and metadata check", flush=True)
        run_env["PGOPTIONS"] = f"-c search_path={schemas[1]}"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_interview_coaching_postgres.py",
                "-q",
                "--tb=short",
            ],
            cwd=PROJECT_DIR,
            env=run_env,
            check=True,
        )
    finally:
        # 唯一删除目标是本次成功创建的随机 Schema，其中仅有合成测试数据。
        for schema in created:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
        if created:
            print(
                "Temporary coaching test schemas removed; existing schemas unchanged.", flush=True
            )


if __name__ == "__main__":
    main()
