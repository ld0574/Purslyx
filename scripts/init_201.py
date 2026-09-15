"""在已经完成 Alembic 迁移的 201 PostgreSQL 中写入固定种子数据。

使用前请通过环境变量注入【本地开发环境.md】中的连接信息；脚本不会清库，也不会读取或
打印密码。表结构统一由 ``scripts/migrate_201.py`` 管理。
"""

from server.app.db import seed_db

if __name__ == "__main__":
    seed_db()
    print("Purslyx PostgreSQL seed data initialized")
