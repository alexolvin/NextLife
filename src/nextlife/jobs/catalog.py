"""Error catalog and exception classification for background jobs (TZ §6.3)."""

from __future__ import annotations

import socket
from typing import Any

import httpx

from nextlife.connectors.base import CapabilityNotSupported, ConnectorError

# Error catalog according to TZ §6.3 table 4
ERROR_CATALOG: dict[str, dict[str, Any]] = {
    "AUTH_REQUIRED": {
        "hint": "Войдите на площадку заново",
        "action_label": "Войти",
        "action_url": "/ui/settings#marketplaces",
    },
    "CAPTCHA": {
        "hint": "Площадка запросила проверку; разместите вручную или повторите позже",
        "action_label": "Повторить",
        "secondary_action_label": "Открыть площадку",
    },
    "VALIDATION": {
        "hint": "Поле объявления нарушает правило площадки",
        "action_label": "Исправить",
    },
    "CATEGORY_INVALID": {
        "hint": "Категория не принята; выберите другую",
        "action_label": "Исправить категорию",
    },
    "PHOTO_REJECTED": {
        "hint": "Фото отклонено площадкой: проверьте формат и качество",
        "action_label": "Исправить фото",
    },
    "LIMIT_REACHED": {
        "hint": "Исчерпан лимит объявлений на площадке",
        "action_label": "Открыть площадку",
    },
    "NETWORK": {
        "hint": "Площадка недоступна; задача повторится автоматически",
        "action_label": "Повторить сейчас",
    },
    "NOT_SUPPORTED": {
        "hint": "Площадка не поддерживает операцию; текст скопирован в буфер",
        "action_label": "Открыть площадку",
    },
    "UNKNOWN": {
        "hint": "Произошла непредвиденная ошибка",
        "action_label": "Повторить",
    },
}


def classify_error(exc: Exception) -> tuple[str, str, str]:
    """Classify an exception into canonical (code, text, hint)."""
    text = str(exc) or exc.__class__.__name__

    if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException, ConnectionError, socket.error)):
        code = "NETWORK"
    elif isinstance(exc, CapabilityNotSupported):
        code = "NOT_SUPPORTED"
    elif "captcha" in text.lower() or "challenge" in text.lower():
        code = "CAPTCHA"
    elif "auth" in text.lower() or "login" in text.lower() or "unauthorized" in text.lower() or "401" in text:
        code = "AUTH_REQUIRED"
    elif "limit" in text.lower() or "quota" in text.lower():
        code = "LIMIT_REACHED"
    elif "category" in text.lower():
        code = "CATEGORY_INVALID"
    elif "photo" in text.lower() or "image" in text.lower():
        code = "PHOTO_REJECTED"
    elif "validat" in text.lower() or "required" in text.lower():
        code = "VALIDATION"
    elif isinstance(exc, ConnectorError):
        code = "UNKNOWN"
    else:
        code = "UNKNOWN"

    hint = ERROR_CATALOG.get(code, {}).get("hint", ERROR_CATALOG["UNKNOWN"]["hint"])
    return code, text, hint
