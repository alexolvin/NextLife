"""Детерминированный анализ качества фото сразу после загрузки (TZ-NextLife-UI-N2 §4 V6, R4).

Подсказки на русском выводятся в UI блоком «Подсказки по качеству»:
поворот (EXIF), тёмный снимок, мелкий снимок, размытие. Чистый PIL —
без внешнего VLM-endpoint, полностью детерминировано.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

# Средний уровень яркости (0-255) ниже порога -> «Снимок тёмный».
DARK_MEAN_LUMINANCE = 35
# Меньшая сторона меньше порога (px) -> «Снимок мелкий».
MIN_SIDE = 500
# Дисперсия карты рёбер меньше порога -> «Снимок размыт».
BLUR_EDGE_VARIANCE = 40.0


def _mean_luminance(img: Image.Image) -> float:
    hist = img.convert("L").histogram()
    total = sum(hist)
    if total == 0:
        return 0.0
    return sum(i * count for i, count in enumerate(hist)) / total


def _edge_variance(img: Image.Image) -> float:
    hist = img.convert("L").filter(ImageFilter.FIND_EDGES).histogram()
    total = sum(hist) or 1
    mean = sum(i * count for i, count in enumerate(hist)) / total
    return sum(count * (i - mean) ** 2 for i, count in enumerate(hist)) / total


def analyze_photo_quality(path: Path | str) -> list[str]:
    """Подсказки по качеству файла фото. Пустой список — замечаний нет."""
    try:
        with Image.open(path) as img:
            hints: list[str] = []
            # EXIF orientation 2-8: снимок повёрнут/отзеркален относительно кадра.
            if img.getexif().get(0x0112) in (2, 3, 4, 5, 6, 7, 8):
                hints.append("Фото повёрнуто — используйте «Выровнять»")
            if _mean_luminance(img) < DARK_MEAN_LUMINANCE:
                hints.append("Снимок тёмный")
            if min(img.size) < MIN_SIDE:
                hints.append("Снимок мелкий — загрузите крупнее")
            if _edge_variance(img) < BLUR_EDGE_VARIANCE:
                hints.append("Снимок размыт")
            return hints
    except Exception:  # noqa: BLE001
        # Нечитаемый файл — это кейс битого фото (P-E05), не подсказки.
        return []
