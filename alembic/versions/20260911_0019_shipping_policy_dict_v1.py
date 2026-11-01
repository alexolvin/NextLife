"""shipping_policy_dict_v1 — TZ-NextLife-UI-N2 раздел 4, V4 (P-E10).

- Единый словарь политики доставки: BUYER_PAYS / SELLER_PAYS / PICKUP_ONLY
  (src/nextlife/core/shipping_policy.py) — мастер, форма правки и
  конструктор доставки работают от одного списка значений.
- INCLUDED -> SELLER_PAYS по всем строкам lot: INCLUDED — устаревшее
  значение (было только в мастере, отсутствовало в форме правки и
  конструкторе доставки).
- Ограничение на уровне БД: CHECK (lot.shipping_policy IN (...)) —
  БД отклоняет значение вне словаря.
- Аудит: SHIPPING_POLICY_REMIGRATION_V4 на каждую изменённую строку;
  вывод «до/после» по каждой строке (TZ N2 §4, V4).

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-11 18:00:00.000000

"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "lot_shipping_policy_chk"
CANONICAL = ("BUYER_PAYS", "SELLER_PAYS", "PICKUP_ONLY")


def _audit(conn, entity: str, entity_id: str, data: dict) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO audit_event (id, at, actor, action, entity, entity_id, data) "
            "VALUES (:id, now(), 'SYSTEM', 'SHIPPING_POLICY_REMIGRATION_V4', "
            ":entity, :eid, CAST(:data AS jsonb))"
        ),
        {
            "id": str(uuid.uuid4()),
            "entity": entity,
            "eid": str(entity_id),
            "data": json.dumps(data, ensure_ascii=False, default=str),
        },
    )


def upgrade() -> None:
    conn = op.get_bind()

    # 1. INCLUDED -> SELLER_PAYS (устаревшее значение словаря).
    rows = conn.execute(
        sa.text("SELECT item_id, shipping_policy FROM lot "
                "WHERE shipping_policy = 'INCLUDED' ORDER BY item_id")
    ).fetchall()
    for item_id, policy in rows:
        print(f"[V4] lot {item_id}: до shipping_policy={policy}; "
              f"после shipping_policy=SELLER_PAYS")
        conn.execute(sa.text("UPDATE lot SET shipping_policy='SELLER_PAYS' "
                             "WHERE item_id=:i"), {"i": item_id})
        _audit(conn, "lot", item_id, {
            "table": "lot",
            "before": {"shipping_policy": policy},
            "after": {"shipping_policy": "SELLER_PAYS"},
        })

    # 2. Ограничение на уровне БД: только значения единого словаря.
    op.execute(
        f"ALTER TABLE lot ADD CONSTRAINT {CONSTRAINT_NAME} "
        f"CHECK (shipping_policy IN "
        f"({', '.join(repr(p) for p in CANONICAL)}))"
    )


def downgrade() -> None:
    # Обратная перекраска данных не выполняется: INCLUDED было
    # артефактом словаря UI, а не реальным бизнес-значением.
    op.execute(f"ALTER TABLE lot DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
