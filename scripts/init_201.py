"""在 201 PostgreSQL 环境初始化 Purslyx 表结构。

使用前请通过环境变量注入【本地开发环境.md】中的连接信息；脚本不会清库，也不会读取或
打印密码。本地开发阶段用 SQLAlchemy create_all 创建表，后续再切换为 Alembic。
"""

from server.app.db import init_db

if __name__ == "__main__":
    init_db()
    print("Purslyx PostgreSQL schema initialized")
