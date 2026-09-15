"""测试进程的无凭据 PostgreSQL 配置占位。"""

import os

# 部分模块在导入时创建 Engine；测试不建立连接，但仍必须满足“仅允许 201 PostgreSQL”约束。
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test-user@10.10.10.201:5432/purslyx",
)
