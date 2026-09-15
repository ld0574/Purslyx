"""隐藏仍引用已删除简历版本的历史匹配池岗位。"""

from __future__ import annotations

from alembic import op

revision = "0005_hide_deleted_resume_pool"
down_revision = "0004_align_runtime_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """修复旧删除流程遗留的可见岗位，并清除其中的来源正文和链接。"""

    op.execute(
        """
        UPDATE job_pool_items AS pool
        SET deleted_at = COALESCE(version.deleted_at, document.deleted_at, now()),
            analysis_status = 'deleted',
            source_url = NULL,
            job_fields = '{}'::jsonb,
            updated_at = now()
        FROM document_versions AS version
        JOIN documents AS document ON document.id = version.document_id
        WHERE pool.resume_version_id = version.id
          AND pool.deleted_at IS NULL
          AND (version.deleted_at IS NOT NULL OR document.deleted_at IS NOT NULL)
        """
    )


def downgrade() -> None:
    """隐私删除后的正文和链接不可恢复，因此降级不伪造历史业务数据。"""
