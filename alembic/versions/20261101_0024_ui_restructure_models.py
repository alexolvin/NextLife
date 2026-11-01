"""0024: UI Restructure — статусы, поля item/listing/job, owner_notification, миграция фреймов.

ТЗ: реструктуризация интерфейса NextLife, Этап 1 (Раздел 2):
1. Статусы Item: READY_FOR_SALE, DISPOSED; миграция старых статусов.
2. Статусы Listing: DRAFT, APPROVED, QUEUED, PUBLISHING, PUBLISHED/ACTIVE, UPDATING, EXPIRED, FAILED, UNPUBLISH_FAILED, ARCHIVED.
3. Поля item: warranty_months, warranty_terms, warranty_until, price_estimate_*, search_text.
4. Поля listing: title, description, category_path, category_key, lang, photo_ids, category_auto, first_published_at, expires_at, last_error_*, is_free.
5. Поле job: batch_id.
6. Новая таблица: owner_notification.
7. Перенос item.attrs.wizard_frames в записи listing (DRAFT/APPROVED).
8. Заполнение item.search_text.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-01 13:40:00.000000
"""

from collections.abc import Sequence
import json
import unicodedata
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    cleaned = str(text).strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    decomposed = unicodedata.normalize("NFKD", cleaned.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Новые поля item
    op.add_column("item", sa.Column("warranty_months", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("warranty_terms", sa.Text(), nullable=True))
    op.add_column("item", sa.Column("warranty_until", sa.Date(), nullable=True))
    op.add_column("item", sa.Column("price_estimate_low", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("item", sa.Column("price_estimate_median", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("item", sa.Column("price_estimate_high", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("item", sa.Column("price_estimate_currency", sa.String(length=3), nullable=True))
    op.add_column("item", sa.Column("price_estimate_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("item", sa.Column("search_text", sa.Text(), nullable=True))

    # 2. Новые поля listing
    op.add_column("listing", sa.Column("title", sa.String(length=255), nullable=True))
    op.add_column("listing", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("listing", sa.Column("category_path", sa.Text(), nullable=True))
    op.add_column("listing", sa.Column("category_key", sa.String(length=100), nullable=True))
    op.add_column("listing", sa.Column("lang", sa.String(length=10), nullable=True))
    op.add_column(
        "listing",
        sa.Column(
            "photo_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "listing",
        sa.Column(
            "category_auto",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column("listing", sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("listing", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("listing", sa.Column("last_error_code", sa.String(length=50), nullable=True))
    op.add_column("listing", sa.Column("last_error_text", sa.Text(), nullable=True))
    op.add_column("listing", sa.Column("last_error_hint", sa.Text(), nullable=True))
    op.add_column(
        "listing",
        sa.Column(
            "is_free",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # 3. Новые поля job
    op.add_column("job", sa.Column("batch_id", sa.String(length=36), nullable=True))
    op.create_index("ix_job_batch_id", "job", ["batch_id"])

    # 4. Таблица owner_notification
    op.create_table(
        "owner_notification",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_owner_notification_at", "owner_notification", ["at"])
    op.create_index("ix_owner_notification_delivered_at", "owner_notification", ["delivered_at"])

    # 5. Миграция статусов item (§2.2)
    bind.execute(
        sa.text("UPDATE item SET status = 'READY_FOR_SALE' WHERE status IN ('AWAITING_APPROVAL', 'APPROVED')")
    )
    bind.execute(
        sa.text(
            "UPDATE item SET status = 'PUBLISHED' "
            "WHERE status IN ('PUBLISHING', 'PARTIALLY_PUBLISHED', 'BLOCKED') "
            "AND EXISTS (SELECT 1 FROM listing WHERE listing.item_code = item.code AND listing.status IN ('PUBLISHED', 'ACTIVE'))"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE item SET status = 'READY_FOR_SALE' "
            "WHERE status IN ('PUBLISHING', 'PARTIALLY_PUBLISHED', 'BLOCKED')"
        )
    )

    # 6. Заполнение item.search_text для существующих предметов
    item_rows = bind.execute(
        sa.text("SELECT code, title, storage_location, description, specs, notes, attrs FROM item")
    ).fetchall()
    for row in item_rows:
        code, title, loc, desc, specs, notes, attrs = row
        tokens = [str(code), f"#{code}"]
        if title:
            tokens.append(str(title))
        if loc:
            tokens.append(str(loc))
        if desc:
            tokens.append(str(desc))
        if specs:
            tokens.append(str(specs))
        if notes:
            tokens.append(str(notes))
        if attrs and isinstance(attrs, dict):
            for k in ("brand", "model", "category"):
                v = attrs.get(k)
                if v:
                    tokens.append(str(v))
        st = _normalize_text(" ".join(tokens))
        bind.execute(
            sa.text("UPDATE item SET search_text = :st WHERE code = :code"),
            {"st": st, "code": code},
        )

    # 7. Перенос item.attrs.wizard_frames в listing (§2.5)
    # Ensure all marketplaces exist in marketplace table to satisfy listing_marketplace_key_fkey
    from pathlib import Path
    import yaml

    mp_dir = Path(__file__).resolve().parent.parent.parent / "config" / "marketplaces"
    if mp_dir.is_dir():
        for ypath in mp_dir.glob("*.yaml"):
            try:
                ydata = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
                m_key = ydata.get("key") or ypath.stem
                m_title = ydata.get("title") or m_key
                m_country = ydata.get("country") or "CZ"
                m_curr = ydata.get("currency") or "CZK"
                m_mode = ydata.get("access_mode") or "DIRECT"
                bind.execute(
                    sa.text(
                        "INSERT INTO marketplace (key, title, country, currency, access_mode, capabilities, enabled, config_hash, created_at, updated_at) "
                        "VALUES (:k, :t, :c, :curr, :mode, '{}', false, 'migration_0024', NOW(), NOW()) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"k": m_key, "t": m_title, "c": m_country, "curr": m_curr, "mode": m_mode},
                )
            except Exception:
                pass

    frame_rows = bind.execute(
        sa.text("SELECT code, target_price, target_currency, description_lang, attrs FROM item")
    ).fetchall()
    for code, price, currency, lang, attrs in frame_rows:
        if not attrs or not isinstance(attrs, dict):
            continue
        frames = attrs.get("wizard_frames")
        if not frames or not isinstance(frames, dict):
            continue
        for mp_key, frame in frames.items():
            if not isinstance(frame, dict):
                continue

            # Ensure mp_key exists in marketplace table before inserting listing
            mp_exists = bind.execute(
                sa.text("SELECT key FROM marketplace WHERE key = :m"),
                {"m": mp_key},
            ).fetchone()
            if not mp_exists:
                bind.execute(
                    sa.text(
                        "INSERT INTO marketplace (key, title, country, currency, access_mode, capabilities, enabled, config_hash, created_at, updated_at) "
                        "VALUES (:k, :t, 'CZ', :curr, 'DIRECT', '{}', false, 'migration_0024', NOW(), NOW()) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"k": mp_key, "t": mp_key, "curr": str(currency or "CZK")},
                )

            # Проверить существующий листинг
            existing = bind.execute(
                sa.text("SELECT id FROM listing WHERE item_code = :c AND marketplace_key = :m"),
                {"c": code, "m": mp_key},
            ).fetchone()
            if existing:
                continue

            frame_status = frame.get("status", "localized")
            listing_status = "APPROVED" if frame_status == "approved" else "DRAFT"
            f_title = frame.get("title") or ""
            f_desc = frame.get("description") or frame.get("text") or ""
            f_cat_path = frame.get("category_path") or ""
            f_cat_key = frame.get("category_key") or frame.get("category_id") or ""
            f_lang = frame.get("lang") or lang or "ru"

            new_id = str(uuid.uuid4())
            curr = str(currency or "CZK")
            p_val = price or 0
            bind.execute(
                sa.text(
                    "INSERT INTO listing (id, item_code, marketplace_key, price, currency, price_base, "
                    "status, publish_evidence, title, description, category_path, category_key, lang, "
                    "photo_ids, category_auto, is_free, created_at, updated_at) "
                    "VALUES (:id, :item_code, :marketplace_key, :price, :currency, :price_base, "
                    ":status, :publish_evidence, :title, :description, :category_path, :category_key, :lang, "
                    ":photo_ids, true, false, NOW(), NOW())"
                ),
                {
                    "id": new_id,
                    "item_code": code,
                    "marketplace_key": mp_key,
                    "price": p_val,
                    "currency": curr,
                    "price_base": p_val,
                    "status": listing_status,
                    "publish_evidence": json.dumps({}),
                    "title": f_title,
                    "description": f_desc,
                    "category_path": f_cat_path,
                    "category_key": f_cat_key,
                    "lang": f_lang,
                    "photo_ids": json.dumps([]),
                },
            )


def downgrade() -> None:
    op.drop_index("ix_owner_notification_delivered_at", table_name="owner_notification")
    op.drop_index("ix_owner_notification_at", table_name="owner_notification")
    op.drop_table("owner_notification")

    op.drop_index("ix_job_batch_id", table_name="job")
    op.drop_column("job", "batch_id")

    op.drop_column("listing", "is_free")
    op.drop_column("listing", "last_error_hint")
    op.drop_column("listing", "last_error_text")
    op.drop_column("listing", "last_error_code")
    op.drop_column("listing", "expires_at")
    op.drop_column("listing", "first_published_at")
    op.drop_column("listing", "category_auto")
    op.drop_column("listing", "photo_ids")
    op.drop_column("listing", "lang")
    op.drop_column("listing", "category_key")
    op.drop_column("listing", "category_path")
    op.drop_column("listing", "description")
    op.drop_column("listing", "title")

    op.drop_column("item", "search_text")
    op.drop_column("item", "price_estimate_at")
    op.drop_column("item", "price_estimate_currency")
    op.drop_column("item", "price_estimate_high")
    op.drop_column("item", "price_estimate_median")
    op.drop_column("item", "price_estimate_low")
    op.drop_column("item", "warranty_until")
    op.drop_column("item", "warranty_terms")
    op.drop_column("item", "warranty_months")
