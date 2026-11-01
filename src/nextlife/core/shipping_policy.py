"""Единый словарь политики доставки (TZ-NextLife-UI-N2 §4 V4, P-E10).

MASTER/правка/конструктор доставки работают от одного и того же списка
значений. INCLUDED — устаревшее значение (было только в мастере, отсутствовало
в форме правки и конструкторе): миграция 0019 перекрасила строки БД в
SELLER_PAYS и поставила CHECK-ограничение; входящее значение INCLUDED
нормализуется к SELLER_PAYS.
"""

SHIPPING_POLICIES: tuple[str, ...] = ("BUYER_PAYS", "SELLER_PAYS", "PICKUP_ONLY")

SHIPPING_POLICY_LABELS_RU: dict[str, str] = {
    "BUYER_PAYS": "Покупатель оплачивает доставку сверху",
    "SELLER_PAYS": "Доставка включена в стоимость",
    "PICKUP_ONLY": "Только личный самовывоз",
}

_LEGACY_ALIASES: dict[str, str] = {"INCLUDED": "SELLER_PAYS"}


def normalize_shipping_policy(value: str | None) -> str | None:
    """Нормализовать значение политики доставки.

    Возвращает каноническое значение из SHIPPING_POLICIES (включая
    нормализацию устаревшего INCLUDED) или None, если значение неизвестно.
    """
    if not value:
        return None
    v = value.strip().upper()
    v = _LEGACY_ALIASES.get(v, v)
    return v if v in SHIPPING_POLICIES else None


def shipping_policy_options() -> list[dict[str, str]]:
    """Опции для <select> мастер/правка — единый источник (TZ N2 V4)."""
    return [{"value": p, "label": SHIPPING_POLICY_LABELS_RU[p]} for p in SHIPPING_POLICIES]
