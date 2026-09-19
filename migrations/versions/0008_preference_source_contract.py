"""统一招聘候选人期望的来源枚举。"""

from __future__ import annotations

from alembic import op

revision = "0008_preference_source"
down_revision = "0007_runtime_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """统一期望来源，并保存 PDF 渲染后的真实页数。"""

    # 0001_baseline 使用当前 ORM 元数据执行 create_all；因此全新数据库可能
    # 已经包含 page_count。旧数据库则可能没有该列，两种情况都必须可升级。
    op.execute("ALTER TABLE resume_exports ADD COLUMN IF NOT EXISTS page_count INTEGER")

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
    op.execute("ALTER TABLE resume_exports DROP COLUMN IF EXISTS page_count")
