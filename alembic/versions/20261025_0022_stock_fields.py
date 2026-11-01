"""0022: Склад — канонические текстовые поля item + nullable listing URL.

ТЗ «Склад» вместо «Ленты», этап 1.1:
1. Прерывание при несовпадении item.code / lot.code.
2. Новые колонки item: description_lang, description, usage, specs, storage_location.
3. Перенос описания из attrs (final_text -> wizard_generated_text.text ->
   generated_description -> listing_text_cs -> description).
4. description_lang = 'ru', если текст преимущественно кириллица.
5. downgrade() удаляет колонки. listing.external_id / external_url — nullable
   (для этапа 2.2; в 0001 уже nullable — alter_column идемпотентен).

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-25 02:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | None = None
depends_on: str | None = None

_DESC_KEYS = (
    "final_text",
    "generated_description",
    "listing_text_cs",
    "description",
)


def _is_cyrillic_dominant(text: str) -> bool:
    """Инлайн-копия draft_text.is_cyrillic_dominant (миграция самодостаточна)."""
    if not text:
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 3:
        return False
    cyrillic = sum(1 for ch in letters if "\u0400" <= ch <= "\u04FF")
    return cyrillic / len(letters) >= 0.5


def _pick_description(attrs: dict | None) -> str | None:
    """Первое непустое значение: final_text -> wizard_generated_text.text ->
    generated_description -> listing_text_cs -> description. Ключ 'notes' не участвует."""
    attrs = attrs or {}
    for key in ("final_text",):
        val = attrs.get(key)
        if val:
            return str(val)
    wgt = attrs.get("wizard_generated_text")
    if isinstance(wgt, dict) and wgt.get("text"):
        return str(wgt["text"])
    for key in ("generated_description", "listing_text_cs", "description"):
        val = attrs.get(key)
        if val:
            return str(val)
    return None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Прерывание при несовпадении кодов item/lot.
    mismatch = bind.execute(
        sa.text(
            "SELECT i.code FROM item i "
            "JOIN lot l ON l.item_id = i.code "
            "WHERE i.code <> l.code ORDER BY i.code"
        )
    ).fetchall()
    if mismatch:
        codes = ", ".join(row[0] for row in mismatch)
        raise ValueError(
            f"Миграция 0022 прервана: item.code != lot.code для кодов: {codes}"
        )

    # 2. Новые колонки item.
    op.add_column("item", sa.Column("description_lang", sa.String(length=10), nullable=True))
    op.add_column("item", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("item", sa.Column("usage", sa.Text(), nullable=True))
    op.add_column("item", sa.Column("specs", sa.Text(), nullable=True))
    op.add_column("item", sa.Column("storage_location", sa.String(length=100), nullable=True))

    # 3+4. Перенос описания из attrs и определение языка.
    rows = bind.execute(sa.text("SELECT code, attrs FROM item")).fetchall()
    for code, attrs in rows:
        desc = _pick_description(attrs)
        if desc:
            lang = "ru" if _is_cyrillic_dominant(desc) else None
            bind.execute(
                sa.text(
                    "UPDATE item SET description = :d, description_lang = :l "
                    "WHERE code = :c"
                ),
                {"d": desc, "l": lang, "c": code},
            )

    # 5. listing.external_id / external_url — nullable (нужно для этапа 2.2).
    # В 0001 уже nullable; alter_column идемпотентен и документирует намерение.
    op.alter_column("listing", "external_id", nullable=True)
    op.alter_column("listing", "external_url", nullable=True)


def downgrade() -> None:
    op.drop_column("item", "storage_location")
    op.drop_column("item", "specs")
    op.drop_column("item", "usage")
    op.drop_column("item", "description")
    op.drop_column("item", "description_lang")
