"""add_lot_reservation_and_deal_constraint

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-08 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add reservation columns to lot table
    op.add_column("lot", sa.Column("reserved_for_party_id", sa.String(length=100), nullable=True))
    op.add_column("lot", sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True))

    # 2. Add lot_id and party_id to deal table, make listing_id nullable
    op.add_column("deal", sa.Column("lot_id", sa.String(length=36), nullable=True))
    op.add_column("deal", sa.Column("party_id", sa.String(length=100), nullable=True))
    op.alter_column("deal", "listing_id", existing_type=sa.String(length=36), nullable=True)

    op.create_foreign_key("fk_deal_lot_id", "deal", "lot", ["lot_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_deal_lot_id", "deal", ["lot_id"])

    # 3. Partial unique index: at most one active deal per lot
    op.create_index(
        "uq_deal_active_lot",
        "deal",
        ["lot_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('CANCELLED', 'EXPIRED', 'FAILED')"),
    )

    # 4. Trigger ensuring second deal cannot be created on reserved lot at DB level
    op.execute("""
        CREATE OR REPLACE FUNCTION check_deal_lot_reservation()
        RETURNS TRIGGER AS $$
        DECLARE
            v_lot_status VARCHAR(50);
            v_count INT;
        BEGIN
            IF NEW.lot_id IS NOT NULL THEN
                SELECT status INTO v_lot_status FROM lot WHERE id = NEW.lot_id;
                IF v_lot_status = 'RESERVED' THEN
                    SELECT COUNT(*) INTO v_count
                    FROM deal
                    WHERE lot_id = NEW.lot_id AND id != NEW.id AND status NOT IN ('CANCELLED', 'EXPIRED', 'FAILED');
                    IF v_count > 0 THEN
                        RAISE EXCEPTION 'Database constraint violation: cannot create second deal on RESERVED lot %', NEW.lot_id
                            USING ERRCODE = 'unique_violation';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_deal_lot_reservation_check ON deal;
        CREATE TRIGGER trg_deal_lot_reservation_check
        BEFORE INSERT OR UPDATE ON deal
        FOR EACH ROW
        EXECUTE FUNCTION check_deal_lot_reservation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_deal_lot_reservation_check ON deal;")
    op.execute("DROP FUNCTION IF EXISTS check_deal_lot_reservation();")
    op.drop_index("uq_deal_active_lot", table_name="deal")
    op.drop_index("ix_deal_lot_id", table_name="deal")
    op.drop_constraint("fk_deal_lot_id", "deal", type_="foreignkey")
    op.alter_column("deal", "listing_id", existing_type=sa.String(length=36), nullable=False)
    op.drop_column("deal", "party_id")
    op.drop_column("deal", "lot_id")
    op.drop_column("lot", "reserved_until")
    op.drop_column("lot", "reserved_for_party_id")
