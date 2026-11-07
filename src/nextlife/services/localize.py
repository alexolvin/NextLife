"""Listing localization and translation service.

Translates classified ad titles and descriptions to the marketplace target language
(e.g., Slovak for Bazoš.sk, Polish for OLX.pl, German for Kleinanzeigen/Willhaben, English for eBay).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from nextlife.core.config import MarketplaceConfig, load_config
from nextlife.services.llm_filter import load_settings

log = logging.getLogger("nextlife.localize")

# Language code to full language name (for prompts)
LANGUAGE_NAMES = {
    "cs": "čeština (Czech)",
    "sk": "slovenčina (Slovak)",
    "pl": "polski (Polish)",
    "de": "deutsch (German)",
    "en": "english (English)",
    "ru": "русский (Russian)",
}

# Rule-based fallback dictionaries for when AI translation is offline
SLOVAK_RULES: list[tuple[str, str]] = [
    (r"\bProdám\b", "Predám"),
    (r"\bprodám\b", "predám"),
    (r"\bKoupím\b", "Kúpim"),
    (r"\bkoupím\b", "kúpim"),
    (r"\bve stavu jako nová\b", "v stave ako nová"),
    (r"\bve stavu jako nový\b", "v stave ako nový"),
    (r"\bve stavu\b", "v stave"),
    (r"\bv perfektním stavu\b", "v perfektnom stave"),
    (r"\bv dobrém stavu\b", "v dobrom stave"),
    (r"\bjako nová\b", "ako nová"),
    (r"\bjako nový\b", "ako nový"),
    (r"\bOriginální krabice je součástí\b", "Pôvodná krabica je súčasťou"),
    (r"\bkrabice\b", "krabica"),
    (r"\bkrabici\b", "krabicu"),
    (r"\bbalení\b", "balenie"),
    (r"\bPošlu kamkoliv po ČR\b", "Pošlem kamkoľvek"),
    (r"\bPošlu\b", "Pošlem"),
    (r"\bpošlu\b", "pošlem"),
    (r"\bkamkoliv\b", "kamkoľvek"),
    (r"\bpo ČR\b", "po SR"),
    (r"\bPreferuji\b", "Preferujem"),
    (r"\bpreferuji\b", "preferujem"),
    (r"\bosobní předání\b", "osobné prevzatie"),
    (r"\bosobní odběr\b", "osobný odber"),
    (r"\botestování funkčnosti\b", "otestovanie funkčnosti"),
    (r"\bpřed koupí\b", "pred kúpou"),
    (r"\bvyzkoušet\b", "vyskúšať"),
    (r"\bmůžu\b", "môžem"),
    (r"\bmůže\b", "môže"),
    (r"\bpoužitá\b", "použitá"),
    (r"\bjen párkrát\b", "iba párkrát"),
    (r"\bpro hraní\b", "na hranie"),
    (r"\bna hraní\b", "na hranie"),
    (r"\bpo technické i vizuální stránce\b", "po technickej aj vizuálnej stránke"),
]

POLISH_RULES: list[tuple[str, str]] = [
    (r"\bProdám\b", "Sprzedam"),
    (r"\bprodám\b", "sprzedam"),
    (r"\bKoupím\b", "Kupię"),
    (r"\bkoupím\b", "kupię"),
    (r"\bve stavu jako nová\b", "w stanie jak nowa"),
    (r"\bve stavu jako nový\b", "w stanie jak nowy"),
    (r"\bv perfektním stavu\b", "w idealnym stanie"),
    (r"\bjako nová\b", "jak nowa"),
    (r"\bjako nový\b", "jak nowy"),
    (r"\bOriginální krabice\b", "Oryginalne pudełko"),
    (r"\bPošlu\b", "Wysyłam"),
    (r"\bosobní předání\b", "odbiór osobisty"),
]

GERMAN_RULES: list[tuple[str, str]] = [
    (r"\bProdám\b", "Verkaufe"),
    (r"\bprodám\b", "verkaufe"),
    (r"\bKoupím\b", "Suche"),
    (r"\bkoupím\b", "suche"),
    (r"\bve stavu jako nová\b", "in neuwertigem Zustand"),
    (r"\bve stavu jako nový\b", "in neuwertigem Zustand"),
    (r"\bv perfektním stavu\b", "in perfektem Zustand"),
    (r"\bjako nová\b", "wie neu"),
    (r"\bjako nový\b", "wie neu"),
    (r"\bOriginální krabice\b", "Originalverpackung"),
    (r"\bkrabice\b", "OVP"),
    (r"\bosobní předání\b", "Selbstabholung"),
]


def get_marketplace_language(mp_cfg: MarketplaceConfig | None, mp_key: str) -> str:
    """Determine ISO language code for a marketplace."""
    if mp_cfg and getattr(mp_cfg, "lang", None):
        return str(mp_cfg.lang).strip().lower()
    if mp_cfg and getattr(mp_cfg, "language", None):
        return str(mp_cfg.language).strip().lower()

    country = (getattr(mp_cfg, "country", None) or "").strip().upper() if mp_cfg else ""
    country_map = {
        "SK": "sk",
        "PL": "pl",
        "DE": "de",
        "AT": "de",
        "US": "en",
        "GB": "en",
        "CZ": "cs",
    }
    if country in country_map:
        return country_map[country]

    if mp_key.endswith("_sk"):
        return "sk"
    if mp_key.endswith("_pl"):
        return "pl"
    if mp_key.endswith("_de") or mp_key == "willhaben_at":
        return "de"
    if mp_key == "ebay":
        return "en"
    return "cs"


def _call_cloudflare_translate(text: str, target_lang: str, source_lang: str = "cs") -> str | None:
    """Translate ad text using Cloudflare Workers AI."""
    settings = load_settings()
    img_ai = settings.get("image_ai", {})
    account_id = img_ai.get("endpoint", "").strip() or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_key = img_ai.get("api_key", "").strip() or os.environ.get("CLOUDFLARE_API_KEY", "").strip()

    if not account_id or not api_key:
        return None

    # Clean account_id if full URL was provided
    if "api.cloudflare.com" in account_id:
        url = account_id
    else:
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct"

    target_name = LANGUAGE_NAMES.get(target_lang, target_lang)
    source_name = LANGUAGE_NAMES.get(source_lang, source_lang)

    system_instruction = (
        f"You are a native professional translator for online classified ads (Bazoš, OLX, eBay, Kleinanzeigen, Willhaben). "
        f"Translate the ad text accurately from {source_name} into natural, native {target_name}. "
        f"Preserve product brand names, model numbers, specifications, prices, and line breaks. "
        f"Do NOT add introductory or concluding remarks, explanations, or quotes. Output ONLY the raw translated text."
    )
    if target_lang == "sk":
        system_instruction += " Important in Slovak: 'Prodám' -> 'Predám', 've stavu' -> 'v stave', 'Pošlu' -> 'Pošlem', 'krabice' -> 'krabica', 'kamkoliv' -> 'kamkoľvek'."

    prompt = f"Text to translate to {target_name}:\n{text}"

    payload = {
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res = data.get("result", {})
            out = ""
            if isinstance(res, dict):
                if "response" in res and res["response"]:
                    out = str(res["response"]).strip()
                elif "choices" in res and res["choices"]:
                    out = str(res["choices"][0].get("message", {}).get("content", "")).strip()
            if out:
                # Strip potential markdown code block wraps
                if out.startswith("```") and out.endswith("```"):
                    lines = out.splitlines()[1:-1]
                    out = "\n".join(lines).strip()
                return out
    except Exception as exc:
        log.warning("Cloudflare translation failed: %s", exc)
    return None


def _apply_rules_fallback(text: str, target_lang: str) -> str:
    """Rule-based translation fallback."""
    result = text
    rules = []
    if target_lang == "sk":
        rules = SLOVAK_RULES
    elif target_lang == "pl":
        rules = POLISH_RULES
    elif target_lang == "de":
        rules = GERMAN_RULES

    for pattern, replacement in rules:
        result = re.sub(pattern, replacement, result)
    return result


def translate_ad_text(text: str, target_lang: str, source_lang: str = "cs") -> str:
    """Translate ad text to target language with AI and fallback."""
    if not text or not text.strip():
        return text

    target_lang = target_lang.strip().lower()
    source_lang = source_lang.strip().lower()
    if target_lang == source_lang:
        return text

    # Try Cloudflare AI first
    translated = _call_cloudflare_translate(text, target_lang=target_lang, source_lang=source_lang)
    if translated and len(translated.strip()) > 3:
        return translated.strip()

    # Fallback to deterministic rules
    return _apply_rules_fallback(text, target_lang)


def translate_listing_content(
    title: str,
    description: str,
    target_lang: str,
    source_lang: str = "cs",
) -> tuple[str, str]:
    """Translate both title and description for a marketplace listing."""
    target_lang = target_lang.strip().lower()
    source_lang = source_lang.strip().lower()

    if target_lang == source_lang:
        return title, description

    tr_title = translate_ad_text(title, target_lang=target_lang, source_lang=source_lang)
    tr_desc = translate_ad_text(description, target_lang=target_lang, source_lang=source_lang)

    return tr_title, tr_desc
