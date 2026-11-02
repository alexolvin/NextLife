"""Wizard step 2: generate a listing title + description.

When the LLM endpoint is configured (Settings → LLM EndPoint), generates a
real selling-oriented title and description from the selected reference
results + owner notes. When the LLM is not configured or the call fails,
returns an honest placeholder — never a raw data dump.
"""

import json
from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.config import ConfigBundle, load_config
from nextlife.storage.models import Item

PLACEHOLDER_TEXT = "Описание будет сгенерировано после подключения LLM"

# Language code -> prepositional form for the generation prompt («на ... языке»).
_GENERATION_LANG_PREP = {
    "ru": "русском",
    "cs": "чешском",
    "en": "английском",
    "de": "немецком",
    "pl": "польском",
}


def _parse_generated(content: str) -> tuple[str, str]:
    """Parse the LLM's ``{"title": ..., "description": ...}`` JSON.

    When the JSON is absent or unparsable, the whole content becomes the
    description and the title stays empty (the UI keeps the old title).
    """
    content = (content or "").strip()
    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start + 1:
            obj = json.loads(content[start:end])
            if isinstance(obj, dict):
                title = str(obj.get("title") or "").strip()
                text = str(obj.get("description") or "").strip()
                if text:
                    return title, text
    except json.JSONDecodeError:
        pass
    return "", content


def generate_description(
    item_code: str,
    selected: list[dict[str, Any]] | None = None,
    notes: str = "",
    config: ConfigBundle | None = None,
    session: Session | None = None,
    lang: str = "ru",
) -> dict[str, Any]:
    """Generate a listing title + description.

    Returns ``title`` (the suggested title, "" when not LLM-generated),
    ``text`` (the description), ``mode`` ("live" when the LLM produced the
    text, "placeholder" otherwise) and ``endpoint_configured``.

    When ``session`` and ``item_code`` are given and no explicit
    ``selected``/``notes`` are provided, the saved values are pulled from the
    Item (``attrs["wizard_selected"]`` and ``notes``). ``lang`` is the
    generation language (``item.description_lang``); the prompt asks the LLM
    to write the listing in that language.
    """
    config = config or load_config()

    if selected is None and session is not None and item_code:
        item = session.get(Item, item_code)
        if item:
            selected = (item.attrs or {}).get("wizard_selected", [])
            if not notes:
                notes = item.notes or ""

    from nextlife.services.llm_filter import call_llm, get_llm_config, is_llm_configured

    llm = get_llm_config()
    endpoint = llm.get("endpoint", "").strip()

    if not is_llm_configured(llm):
        return {
            "item_code": item_code,
            "title": "",
            "text": PLACEHOLDER_TEXT,
            "mode": "placeholder",
            "endpoint_configured": False,
        }

    ref_lines = "\n".join(
        f"- {r.get('title', '')} — {r.get('price', '')} {r.get('currency', '')}".rstrip(" —")
        for r in (selected or [])
        if r.get("title")
    )
    lang_prep = _GENERATION_LANG_PREP.get(lang, "русском")
    prompt = (
        f"Придумай заголовок и продающее описание объявления на {lang_prep} языке "
        "на основе эталонных объявлений и заметок владельца.\n"
        "Заголовок: до 60 символов, конкретный (модель, ключевые характеристики).\n"
        f"Эталонные объявления:\n{ref_lines or '(нет)'}\n"
        f"Заметки владельца: {notes or '(нет)'}\n\n"
        'Верни только JSON: {"title": "...", "description": "..."} — '
        "описание 4-7 предложений, без заголовков и пояснений."
    )
    try:
        content = call_llm(
            endpoint,
            llm.get("api_key", "").strip(),
            llm.get("model", "").strip(),
            prompt,
        )
    except Exception:
        return {
            "item_code": item_code,
            "title": "",
            "text": PLACEHOLDER_TEXT,
            "mode": "placeholder",
            "endpoint_configured": True,
        }
    title, text = _parse_generated(content)
    if not text:
        return {
            "item_code": item_code,
            "title": "",
            "text": PLACEHOLDER_TEXT,
            "mode": "placeholder",
            "endpoint_configured": True,
        }
    return {
        "item_code": item_code,
        "title": title,
        "text": text,
        "mode": "live",
        "endpoint_configured": True,
    }
