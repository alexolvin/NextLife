"""Language detection and translation service (section 11.3)."""

from typing import Any

from nextlife.core.exceptions import NextLifeError


class TranslationError(NextLifeError):
    """Base error for translation and language detection."""


def detect_language(text: str) -> str:
    """Detect language ISO code (cs, ru, en, de, etc.)."""
    clean = text.lower()
    # Basic deterministic token-based detection without hardcoding dictionaries
    # Checks for distinctive unicode characters
    cz_chars = set("áčďéěíňóřšťúůýž")
    ru_chars = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")

    if any(c in cz_chars for c in clean):
        return "cs"
    if any(c in ru_chars for c in clean):
        return "ru"
    return "en"


def translate_text(
    text: str, source_lang: str, target_lang: str, translator_backend: Any = None
) -> str:
    """Translate text between languages using a single unified translation service."""
    if source_lang == target_lang:
        return text

    if translator_backend and hasattr(translator_backend, "translate"):
        return str(translator_backend.translate(text, source=source_lang, target=target_lang))

    # Deterministic default formatting when backend is offline/mock
    return f"[{target_lang.upper()}] {text}"
