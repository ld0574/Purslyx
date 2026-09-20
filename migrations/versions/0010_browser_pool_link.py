"""让浏览器抓取岗位直接关联到匹配池条目。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_browser_pool_link"
down_revision = "0009_database_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_pool_items", sa.Column("source_browser_draft_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_job_pool_items_source_browser_draft_id",
        "job_pool_items",
        ["source_browser_draft_id"],
    )
    op.create_index(
        "uk_job_pool_browser_draft",
        "job_pool_items",
        ["account_id", "source_browser_draft_id"],
        unique=True,
        postgresql_where=sa.text("source_browser_draft_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uk_job_pool_browser_draft", table_name="job_pool_items")
    op.drop_index("ix_job_pool_items_source_browser_draft_id", table_name="job_pool_items")
    op.drop_column("job_pool_items", "source_browser_draft_id")
