"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-09-07 16:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. item
    op.create_table(
        "item",
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category_key", sa.String(length=50), nullable=False),
        sa.Column("attrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("condition_key", sa.String(length=50), nullable=False),
        sa.Column("defects", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("target_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("target_currency", sa.String(length=3), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    # 2. item_photo
    op.create_table(
        "item_photo",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("derived_from_id", sa.String(length=36), nullable=True),
        sa.Column("transform_log", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ai_generated", sa.Boolean(), nullable=False),
        sa.Column("vlm_verdict", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["derived_from_id"],
            ["item_photo.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_code"],
            ["item.code"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 3. marketplace
    op.create_table(
        "marketplace",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("access_mode", sa.String(length=20), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    # 4. research_run
    op.create_table(
        "research_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("marketplaces", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_code"],
            ["item.code"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. research_result
    op.create_table(
        "research_result",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_key", sa.String(length=50), nullable=False),
        sa.Column("external_url", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_base", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("fx_rate_date", sa.Date(), nullable=False),
        sa.Column("condition_text", sa.String(length=100), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["marketplace_key"],
            ["marketplace.key"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 6. listing
    op.create_table(
        "listing",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("marketplace_key", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("external_url", sa.String(length=1024), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_base", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_code"],
            ["item.code"],
        ),
        sa.ForeignKeyConstraint(
            ["marketplace_key"],
            ["marketplace.key"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 7. thread
    op.create_table(
        "thread",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("counterparty", sa.String(length=255), nullable=False),
        sa.Column("lang", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 8. message
    op.create_table(
        "message",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text_original", sa.Text(), nullable=False),
        sa.Column("lang_original", sa.String(length=10), nullable=False),
        sa.Column("text_ru", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.String(length=20), nullable=True),
        sa.Column("template_key", sa.String(length=50), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["thread.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 9. deal
    op.create_table(
        "deal",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("agreed_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_base", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("delivery_method", sa.String(length=50), nullable=False),
        sa.Column("payment_method", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 10. fx_rate
    op.create_table(
        "fx_rate",
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("rate_to_base", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("rate_date", "currency"),
    )

    # 11. buy_request
    op.create_table(
        "buy_request",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query", sa.String(length=255), nullable=False),
        sa.Column("attrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("budget", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 12. buy_candidate
    op.create_table(
        "buy_candidate",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_key", sa.String(length=50), nullable=False),
        sa.Column("external_url", sa.String(length=1024), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_base", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("contacted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["marketplace_key"],
            ["marketplace.key"],
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["buy_request.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 13. approval
    op.create_table(
        "approval",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_code"],
            ["item.code"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payload_hash"),
    )

    # 14. job
    op.create_table(
        "job",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )

    # 15. audit_event
    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 16. truth_log
    op.create_table(
        "truth_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("recipient", sa.String(length=30), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "evidence IS NOT NULL AND evidence != '{}'::jsonb", name="truth_log_evidence_not_empty"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Trigger to prevent UPDATE and DELETE on audit_event
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_update_delete()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'UPDATE and DELETE operations are forbidden on audit_event table';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_event_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_update_delete();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_update_delete ON audit_event;")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_update_delete();")
    op.drop_table("truth_log")
    op.drop_table("audit_event")
    op.drop_table("job")
    op.drop_table("approval")
    op.drop_table("buy_candidate")
    op.drop_table("buy_request")
    op.drop_table("fx_rate")
    op.drop_table("deal")
    op.drop_table("message")
    op.drop_table("thread")
    op.drop_table("listing")
    op.drop_table("research_result")
    op.drop_table("research_run")
    op.drop_table("marketplace")
    op.drop_table("item_photo")
    op.drop_table("item")
