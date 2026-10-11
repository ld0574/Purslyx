"""新增账号自动匹配授权与持久待处理队列，不改变旧分析。"""

import sqlalchemy as sa
from alembic import op

revision = "0012_auto_matching"
down_revision = "0011_interview_coaching"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    # 当前 ORM 基线也会创建这两张表；旧库增量升级则走显式固定 Schema。
    if "auto_matching_configs" not in tables:
        op.create_table(
            "auto_matching_configs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("resume_document_id", sa.Integer()),
            sa.Column("preference_ids", sa.JSON(), nullable=False),
            sa.Column("daily_limit", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True)),
            sa.Column("idempotency_key", sa.String(128)),
            sa.Column("request_hash", sa.String(64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("account_id", name="uk_auto_matching_account"),
            sa.CheckConstraint("daily_limit BETWEEN 1 AND 50", name="ck_auto_matching_daily_limit"),
            sa.CheckConstraint("revision >= 1", name="ck_auto_matching_revision"),
        )
        for column in ("public_id", "account_id", "resume_document_id"):
            op.create_index(
                f"ix_auto_matching_configs_{column}",
                "auto_matching_configs",
                [column],
                unique=column == "public_id",
            )
    if "auto_match_jobs" not in tables:
        op.create_table(
            "auto_match_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("config_id", sa.Integer(), nullable=False),
            sa.Column("config_revision", sa.Integer(), nullable=False),
            sa.Column("job_pool_item_id", sa.Integer(), nullable=False),
            sa.Column("source_key", sa.String(64), nullable=False),
            sa.Column("resume_version_id", sa.Integer()),
            sa.Column("preference_version_ids", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("blocked_code", sa.String(80)),
            sa.Column("scheduled_date", sa.String(10)),
            sa.Column("analysis_id", sa.Integer()),
            sa.Column("task_id", sa.Integer()),
            sa.Column("request_id", sa.String(64)),
            sa.Column("deleted_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("job_pool_item_id", name="uk_auto_match_pool"),
            sa.UniqueConstraint("account_id", "source_key", name="uk_auto_match_source"),
            sa.CheckConstraint(
                "state IN ('waiting', 'enqueued', 'cancelled')", name="ck_auto_match_state"
            ),
        )
        for column in (
            "public_id",
            "account_id",
            "config_id",
            "job_pool_item_id",
            "resume_version_id",
            "scheduled_date",
            "analysis_id",
            "task_id",
            "deleted_at",
        ):
            op.create_index(
                f"ix_auto_match_jobs_{column}",
                "auto_match_jobs",
                [column],
                unique=column == "public_id",
            )
        op.create_index(
            "ix_auto_match_waiting", "auto_match_jobs", ["account_id", "state", "created_at"]
        )


def downgrade() -> None:
    raise RuntimeError("自动匹配迁移仅允许前向升级；代码回退保留授权和任务记录。")
