"""currency_model_v1 — TZ-NextLife-UI-N2 раздел 1.

- lot.price_base / lot.base_rate_date — внутренняя отчётная сверка
  в главной валюте (NULL, пока в fx_rate нет курсов — конвертация
  запрещена ТЗ, честные NULL).
- Семена лотов EUR -> CZK БЕЗ ИЗМЕНЕНИЯ ЧИСЛА: все целевые площадки
  работают в CZK, это поправка метки, а не конвертация.
- item.target_price / item.target_currency синхронизируются из лота
  с ценой (лот — источник истины): закрывает расхождение 348
  (60 EUR -> 1850 CZK по лоту) и пустую 417 (NULL -> 1500 CZK).
- Сделки и расчёты EUR -> CZK (число без изменения).
- Аудит: CURRENCY_REMIGRATION_V1 на каждую изменённую строку;
  вывод «до/после» по каждой изменённой строке (ТЗ §4, V1).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-11 12:00:00.000000

"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit(conn, entity: str, entity_id: str, data: dict) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO audit_event (id, at, actor, action, entity, entity_id, data) "
            "VALUES (:id, now(), 'SYSTEM', 'CURRENCY_REMIGRATION_V1', "
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

    # 0. Внутренние отчётные колонки лота (fx_rate пуст — честные NULL).
    op.add_column("lot", sa.Column("price_base", sa.Numeric(12, 2), nullable=True))
    op.add_column("lot", sa.Column("base_rate_date", sa.Date(), nullable=True))
    op.execute("UPDATE lot SET price_base = NULL, base_rate_date = NULL")

    # 1. Лоты: EUR -> CZK, число без изменения.
    rows = conn.execute(
        sa.text("SELECT item_id, price_gross, currency FROM lot "
                "WHERE currency <> 'CZK' ORDER BY item_id")
    ).fetchall()
    for item_id, price_gross, cur in rows:
        print(f"[V1] lot {item_id}: до currency={cur} price_gross={price_gross}; "
              f"после currency=CZK price_gross={price_gross}")
        conn.execute(sa.text("UPDATE lot SET currency='CZK' WHERE item_id=:i"),
                     {"i": item_id})
        _audit(conn, "lot", item_id, {
            "table": "lot",
            "before": {"currency": cur, "price_gross": str(price_gross)},
            "after": {"currency": "CZK", "price_gross": str(price_gross)},
        })

    # 2. item.target_* из лота с ценой (лот — источник истины).
    rows = conn.execute(
        sa.text(
            "SELECT l.item_id, l.price_gross, l.currency, "
            "i.target_price, i.target_currency "
            "FROM lot l JOIN item i ON i.code = l.item_id "
            "WHERE l.price_gross IS NOT NULL ORDER BY l.item_id"
        )
    ).fetchall()
    for item_id, price_gross, cur, old_price, old_cur in rows:
        if str(old_price) == str(price_gross) and old_cur == cur:
            continue
        print(f"[V1] item {item_id}: до target_price={old_price} "
              f"target_currency={old_cur}; после target_price={price_gross} "
              f"target_currency={cur}")
        conn.execute(
            sa.text("UPDATE item SET target_price=:p, target_currency=:c "
                    "WHERE code=:i"),
            {"p": price_gross, "c": cur, "i": item_id},
        )
        _audit(conn, "item", item_id, {
            "table": "item",
            "before": {"target_price": None if old_price is None else str(old_price),
                       "target_currency": old_cur},
            "after": {"target_price": str(price_gross), "target_currency": cur},
        })

    # 3. Сделки: EUR -> CZK, число без изменения.
    rows = conn.execute(
        sa.text("SELECT id, agreed_price, currency FROM deal "
                "WHERE currency <> 'CZK' ORDER BY created_at")
    ).fetchall()
    for deal_id, agreed_price, cur in rows:
        print(f"[V1] deal {deal_id}: до currency={cur} agreed_price={agreed_price}; "
              f"после currency=CZK agreed_price={agreed_price}")
        conn.execute(sa.text("UPDATE deal SET currency='CZK' WHERE id=:d"),
                     {"d": deal_id})
        _audit(conn, "deal", deal_id, {
            "table": "deal",
            "before": {"currency": cur, "agreed_price": str(agreed_price)},
            "after": {"currency": "CZK", "agreed_price": str(agreed_price)},
        })

    # 4. Расчёты (settlement): EUR -> CZK, числа без изменения.
    rows = conn.execute(
        sa.text("SELECT deal_id, currency FROM settlement WHERE currency <> 'CZK'")
    ).fetchall()
    for deal_id, cur in rows:
        print(f"[V1] settlement {deal_id}: до currency={cur}; после currency=CZK")
        conn.execute(sa.text("UPDATE settlement SET currency='CZK' WHERE deal_id=:d"),
                     {"d": deal_id})
        _audit(conn, "settlement", str(deal_id), {
            "table": "settlement",
            "before": {"currency": cur},
            "after": {"currency": "CZK"},
        })


def downgrade() -> None:
    # Обратная перекраска данных не выполняется: метки EUR были
    # артефактом seed-данных, а не реальной конвертацией.
    op.drop_column("lot", "base_rate_date")
    op.drop_column("lot", "price_base")
