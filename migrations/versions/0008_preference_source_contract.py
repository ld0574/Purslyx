"""统一招聘候选人期望的来源枚举。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_preference_source"
down_revision = "0007_runtime_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """统一期望来源，并保存 PDF 渲染后的真实页数。"""

    op.add_column("resume_exports", sa.Column("page_count", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE job_preference_versions
           SET source_type = 'candidate_disclosed'
         WHERE source_type = 'candidate_confirmed';

        UPDATE job_preferences
           SET source_type = 'candidate_disclosed'
         WHERE subject_document_id IS NOT NULL
           AND source_type IN ('self_confirmed', 'candidate_confirmed');
        """
    )


def downgrade() -> None:
    """移除页数并恢复旧版本曾使用的来源值。"""

    op.execute(
        """
        UPDATE job_preference_versions
           SET source_type = 'candidate_confirmed'
         WHERE source_type = 'candidate_disclosed';

        UPDATE job_preferences
           SET source_type = 'self_confirmed'
         WHERE subject_document_id IS NOT NULL
           AND source_type = 'candidate_disclosed';
        """
    )
    op.drop_column("resume_exports", "page_count")
