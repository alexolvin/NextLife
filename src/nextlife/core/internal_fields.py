"""Барьер сериализации внутренних полей (TZ-NextLife-UI-N2 §1.2).

price_base / base_rate_date — внутренняя сверка суммы в главной
(отчётной) валюте. Они не должны попадать в исходящие данные:
шаблон объявления, черновик, предпросмотр, сообщение. Это
техническая гарантия, а не договорённость: любая попытка
подставить внутреннее поле в исходящий payload вызывает
исключение.
"""

from __future__ import annotations

from typing import Any

INTERNAL_FIELDS: frozenset[str] = frozenset({"price_base", "base_rate_date"})


class InternalFieldLeakError(Exception):
    """Исходящий payload содержит внутреннее (не публикуемое) поле."""

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__(
            "Внутренние поля в исходящих данных: " + ", ".join(fields)
        )


def assert_outgoing_clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Убедиться, что в исходящий payload не попали внутренние поля.

    Возвращает payload как есть; при нарушении — InternalFieldLeakError.
    """
    leaked = sorted(set(payload) & INTERNAL_FIELDS)
    if leaked:
        raise InternalFieldLeakError(leaked)
    return payload
