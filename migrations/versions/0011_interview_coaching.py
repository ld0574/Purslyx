"""新增面试规则版本和独立重答训练，保留旧记录。"""

import sqlalchemy as sa
from alembic import op

revision = "0011_interview_coaching"
down_revision = "0010_browser_pool_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "rubric_version" not in {row["name"] for row in inspector.get_columns("interview_sessions")}:
        op.add_column(
            "interview_sessions", sa.Column("rubric_version", sa.String(80), nullable=True)
        )
    if "ix_interview_sessions_rubric_version" not in {
        row["name"] for row in inspector.get_indexes("interview_sessions")
    }:
        op.create_index(
            "ix_interview_sessions_rubric_version", "interview_sessions", ["rubric_version"]
        )
    constraints = {
        row["name"]: row["sqltext"] for row in inspector.get_check_constraints("async_tasks")
    }
    if "interview_practice" not in constraints.get("ck_async_tasks_type", ""):
        if "ck_async_tasks_type" in constraints:
            op.drop_constraint("ck_async_tasks_type", "async_tasks", type_="check")
        op.create_check_constraint(
            "ck_async_tasks_type",
            "async_tasks",
            "task_type IN ('document_parse', 'analysis', 'rewrite', 'resume_export', 'interview_opening', 'interview_feedback', 'interview_summary', 'interview_practice', 'log_export')",
        )
    # 基线和旧开发初始化会通过当前 ORM 建表；两条启动路径都允许重复迁移。
    if "interview_practices" in inspector.get_table_names():
        return
    op.create_table(
        "interview_practices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("interview_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("original_answer_id", sa.Integer(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("rubric_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("feedback", sa.JSON()),
        sa.Column("comparison", sa.JSON()),
        sa.Column("task_id", sa.Integer()),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("interview_id", "question_id", name="uk_interview_practice_question"),
        sa.UniqueConstraint(
            "account_id", "idempotency_key", name="uk_interview_practice_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'available', 'failed')",
            name="ck_interview_practice_status",
        ),
    )
    for column in (
        "public_id",
        "account_id",
        "interview_id",
        "question_id",
        "original_answer_id",
        "task_id",
        "deleted_at",
    ):
        op.create_index(
            f"ix_interview_practices_{column}",
            "interview_practices",
            [column],
            unique=column == "public_id",
        )


def downgrade() -> None:
    raise RuntimeError("面试训练迁移仅允许前向升级；代码回退请保留训练表和新数据。")
