"""normalize_item_defects

N4-01: поле item.defects приводится к единственной форме —
список объектов {zone, text, photo_id}.

Перенос существующих записей обеих форм:
  - mapping {zone: text}      -> [{zone, text, photo_id: null}, ...]
  - array [{zone, text}, ...] -> [{zone, text, photo_id}, ...] (photo_id: null,
                                   если ключа не было)
  - JSON null / SQL NULL      -> []

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-10 20:40:00.000000

"""

from collections.abc import Sequence

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    # 1. mapping {zone: text} -> список объектов
    op.execute(
        """
        UPDATE item
        SET defects = (
            SELECT COALESCE(
                jsonb_agg(jsonb_build_object('zone', k, 'text', v, 'photo_id', NULL)
                          ORDER BY k),
                '[]'::jsonb
            )
            FROM jsonb_each_text(defects) AS t(k, v)
        )
        WHERE jsonb_typeof(defects) = 'object'
        """
    )

    # 2. элементы массива приводятся ровно к форме {zone, text, photo_id}
    op.execute(
        """
        UPDATE item
        SET defects = (
            SELECT COALESCE(
                jsonb_agg(jsonb_build_object(
                    'zone', e ->> 'zone',
                    'text', e ->> 'text',
                    'photo_id', e ->> 'photo_id'
                ) ORDER BY o.ord),
                '[]'::jsonb
            )
            FROM jsonb_array_elements(defects) WITH ORDINALITY AS o(e, ord)
        )
        WHERE jsonb_typeof(defects) = 'array'
        """
    )

    # 3. JSON null / SQL NULL -> []
    op.execute(
        """
        UPDATE item
        SET defects = '[]'::jsonb
        WHERE defects IS NULL OR jsonb_typeof(defects) = 'null'
        """
    )


def downgrade() -> None:
    # Обратный перенос невозможен по определению: после нормализации
    # не сохраняется информация, какая исходная форма (mapping или array)
    # была у каждой записи. Откат намеренно не выполняется.
    pass
