"""岗位不可变来源键与本人确认的处理状态；不推断投递、不重算报告。"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse

import sqlalchemy as sa
from alembic import op

revision = "0013_job_handling"
down_revision = "0012_auto_matching"
branch_labels = None
depends_on = None


def _frozen_source_url(platform: str, value: str) -> str | None:
    """冻结 v1 身份规则，不依赖未来 ORM 变更，也不改原平台访问链接。"""
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        domain = {"boss": "zhipin.com", "liepin": "liepin.com"}.get(platform)
        if parsed.scheme != "https" or not domain or host not in {domain, f"www.{domain}"}:
            return None
        path = parsed.path.rstrip("/") or "/"
        stable_path = (platform == "boss" and re.fullmatch(r"/job_detail/[A-Za-z0-9_-]+\.html", path)) or (
            platform == "liepin" and re.fullmatch(r"/job/[A-Za-z0-9_-]+\.(?:shtml|html)", path)
        )
        query = [] if stable_path else sorted((key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                                               if not key.lower().startswith("utm_") and key.lower() not in {"spm", "ka", "tracking", "trackingid"})
        return f"https://www.{domain}{path}" + ("?" + urlencode(query) if query else "")
    except (ValueError, TypeError):
        return None


def backfill_source_keys(connection) -> None:
    rows = connection.execute(sa.text("SELECT id, platform, source_url FROM job_pool_items WHERE source_type='browser_capture' AND source_url IS NOT NULL").execution_options(yield_per=200))
    for row in rows:
        normalized = _frozen_source_url(row.platform, row.source_url)
        if normalized:
            key = f"browser:{row.platform}:" + hashlib.sha256(normalized.encode()).hexdigest()
            connection.execute(sa.text("UPDATE job_pool_items SET opportunity_key=:key WHERE id=:id"), {"key": key, "id": row.id})


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {row["name"] for row in inspector.get_columns("job_pool_items")}
    if "opportunity_key" not in columns:
        op.add_column("job_pool_items", sa.Column("opportunity_key", sa.String(128), nullable=True))
        op.execute(sa.text("""
            UPDATE job_pool_items SET opportunity_key = CASE
              WHEN source_type = 'browser_capture' AND COALESCE(platform, '') <> '' AND COALESCE(source_url_hash, '') <> ''
              THEN 'browser:' || platform || ':' || source_url_hash
              ELSE 'manual:' || public_id END
        """))
        backfill_source_keys(op.get_bind())
        op.alter_column("job_pool_items", "opportunity_key", nullable=False)
    indexes = {row["name"] for row in inspector.get_indexes("job_pool_items")}
    if "ix_job_pool_opportunity" not in indexes:
        op.create_index("ix_job_pool_opportunity", "job_pool_items", ["account_id", "opportunity_key", "created_at"])
    tables = set(inspector.get_table_names())
    if "job_handling_states" not in tables:
        op.create_table(
            "job_handling_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("opportunity_key", sa.String(128), nullable=False),
            sa.Column("reference_pool_item_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("account_id", "opportunity_key", name="uk_job_handling_source"),
            sa.CheckConstraint("status IN ('pending', 'verifying', 'applied', 'dismissed')", name="ck_job_handling_status"),
            sa.CheckConstraint("revision >= 1", name="ck_job_handling_revision"),
        )
        for column in ("public_id", "account_id", "reference_pool_item_id"):
            op.create_index(f"ix_job_handling_states_{column}", "job_handling_states", [column], unique=column == "public_id")
    if "job_handling_changes" not in tables:
        op.create_table(
            "job_handling_changes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("handling_state_id", sa.Integer(), nullable=False),
            sa.Column("reference_pool_item_id", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(128), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("previous_status", sa.String(24), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("account_id", "idempotency_key", name="uk_job_handling_change_key"),
        )
        for column in ("public_id", "account_id", "handling_state_id", "reference_pool_item_id"):
            op.create_index(f"ix_job_handling_changes_{column}", "job_handling_changes", [column], unique=column == "public_id")


def downgrade() -> None:
    raise RuntimeError("代码回退保留岗位来源键及本人确认的处理状态，禁止删表回滚。")
