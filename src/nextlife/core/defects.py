"""Дефекты и особенности — раздельные сущности (TZ-NextLife-UI-N2 §4 V4, P-E07).

Дефект — отклонение от нормального состояния (царапина, потёртость, шум,
пыль). Позитивное описание («идеальное состояние, без следов») —
особенность, а не дефект: в красном блоке «Дефекты» его не показывают
(честность Э-1 — ярлык соответствует содержимому), оно выводится
отдельным блоком «Особенности».
"""

from typing import Any

POSITIVE_MARKERS: tuple[str, ...] = (
    "идеальн",
    "отличн",
    "безупречн",
    "как нов",
    "без следов",
    "без царапин",
    "без дефектов",
    "без повреждений",
    "без изъянов",
    "perfect",
    "excellent",
    "mint condition",
    "like new",
    "no scratches",
    "no defects",
)


def is_positive_feature(defect: dict[str, Any]) -> bool:
    """Запись описывает состояние, а не отклонение (позитивная формулировка)."""
    text = str(defect.get("text") or "").lower()
    return any(marker in text for marker in POSITIVE_MARKERS)


def split_defects_and_features(
    defects: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Разделить записи на (реальные дефекты, особенности), порядок сохраняется."""
    entries = list(defects or [])
    real = [d for d in entries if not is_positive_feature(d)]
    features = [d for d in entries if is_positive_feature(d)]
    return real, features
