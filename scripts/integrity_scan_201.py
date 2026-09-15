#!/usr/bin/env python3
"""在《本地开发环境.md》的 201 PostgreSQL 上执行引用完整性巡检。"""

from __future__ import annotations

import argparse
import json

from migrate_201 import configure_environment


def main() -> None:
    parser = argparse.ArgumentParser(description="巡检 Purslyx 201 PostgreSQL 的逻辑引用完整性")
    parser.add_argument("--max-findings", type=int, default=1000, help="最多持久化的脱敏发现数量")
    parser.add_argument("--no-fail", action="store_true", help="发现异常时仍返回退出码 0")
    args = parser.parse_args()

    # 必须先从唯一环境文件固定 201 连接，再导入会创建 Engine 的服务端模块。
    configure_environment()
    from server.app.db import session_scope
    from server.app.integrity import run_integrity_scan

    with session_scope() as db:
        run = run_integrity_scan(db, max_persisted_findings=args.max_findings)
        result = {
            "environment": "201",
            "database": "postgresql",
            "status": run.status,
            "summary": run.summary,
        }
    print(json.dumps(result, ensure_ascii=False))
    if run.status != "clean" and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
