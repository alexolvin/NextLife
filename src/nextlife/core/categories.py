"""Справочник категорий (TZ-NextLife-UI-N2 §4 V4, P-E19).

Единый источник категорий = файлы config/categories/<key>.yaml (ключ — имя
файла). Мастер и все экранные выборки категорий читают его через
load_category_options: категория, которой нет в справочнике, не существует
для системы, а каждая категория справочника доступна в мастере.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CATEGORY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "categories"


@lru_cache(maxsize=1)
def load_category_options() -> tuple[tuple[str, str], ...]:
    """Справочник категорий: пары (ключ, заголовок), отсортированы по ключу.

    Файл без ключа не считается объявленной категорией.
    """
    out: dict[str, str] = {}
    if CATEGORY_DIR.is_dir():
        for f in sorted(CATEGORY_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            key = data.get("key")
            if key:
                out[str(key)] = str(data.get("title") or key)
    return tuple(sorted(out.items()))


def category_title(key: str | None) -> str | None:
    """Русский заголовок категории или None, если категория не объявлена."""
    if not key:
        return None
    return dict(load_category_options()).get(key)


def declared_category_keys() -> frozenset[str]:
    return frozenset(k for k, _ in load_category_options())
