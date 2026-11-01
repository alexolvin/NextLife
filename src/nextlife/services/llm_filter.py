"""LLM-based product-type filter for wizard search results.

When the owner searches for "Samsung S20", the filter should show
product-type categories (e.g. «Только видеокарта», «Готовый ПК»), not
marketplace names. This service calls the configured LLM endpoint to
classify the results by product type.

When the LLM is not configured (empty endpoint) or the call fails, the
function returns ``None`` — the UI then hides the filter element entirely
(no fabricated categories, no platform fallback).
"""

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nextlife.core.config import get_data_dir

log = logging.getLogger("nextlife.llm_filter")


def _settings_path() -> Path:
    """Settings live in the writable data dir.

    Production runs with ``ProtectSystem=strict`` (only ``NEXTLIFE_DATA_DIR``
    is writable), so the app ``config/`` dir is read-only at runtime. The data
    dir (same base as media) is the stable writable location.
    """
    return get_data_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {"llm": {}, "marketplace_overrides": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"llm": {}, "marketplace_overrides": {}}
    return data if isinstance(data, dict) else {"llm": {}, "marketplace_overrides": {}}


def save_settings(data: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_llm_config() -> dict[str, str]:
    llm = load_settings().get("llm", {})
    if not isinstance(llm, dict):
        return {}
    return {str(k): str(v) for k, v in llm.items()}


# Marketplace/seller secrets, editable from the web Settings page. Stored in
# settings.json (data dir) because production is read-only except the data dir
# (ProtectSystem=strict), so the .env file cannot be written at runtime.
SECRET_KEYS = ("BAZOS_PHONE", "SELLER_NAME", "SELLER_PSC", "BAZOS_PASSWORD")

# Per-marketplace secrets: marketplace key -> [(secret_key, label, input_type, placeholder)].
# Only marketplaces that need credentials for publish list entries here.
# Aukro publish is via the FEED channel (no login/password), so it has no secrets.
MP_SECRETS: dict[str, list[tuple[str, str, str, str]]] = {
    "bazos_cz": [
        ("BAZOS_PHONE", "Телефон (SMS-подтверждение)", "text", "+420..."),
        ("BAZOS_PASSWORD", "Пароль аккаунта Bazoš", "password", "••••••"),
    ],
}

# General seller data (not tied to one marketplace).
SELLER_SECRETS: list[tuple[str, str, str, str]] = [
    ("SELLER_NAME", "Имя / название", "text", "имя"),
    ("SELLER_PSC", "PSC / адрес", "text", "PSC / адрес"),
]


def get_secrets() -> dict[str, str]:
    secrets = load_settings().get("secrets", {})
    if not isinstance(secrets, dict):
        return {}
    return {str(k): str(v) for k, v in secrets.items()}


def call_llm(endpoint: str, api_key: str, model: str, prompt: str) -> str:
    """POST a chat-completion request and return the raw message content."""
    # Accept either the full chat-completions URL or the base URL (e.g.
    # https://api.openai.com/v1); append the path if it's missing.
    url = endpoint.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0,
    }
    # Reasoning-модели (Qwen3 на vLLM) тратят бюджет токенов на «размышления»
    # и возвращают content=null → фильтр не парсится. Отключаем thinking;
    # провайдеры, не знающие параметр (OpenAI и др.), отвечают 400 — тогда
    # повторяем запрос без него.
    try:
        data = _post_chat(url, api_key, {**payload, "chat_template_kwargs": {"enable_thinking": False}})
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        data = _post_chat(url, api_key, payload)
    content = data["choices"][0]["message"]["content"]
    return str(content).strip()


def _post_chat(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    # 30 с: локальная модель отвечает дольше 10 с на классификацию, прежний
    # timeout=10 убивал вызов → фильтр скрыт.
    with urllib.request.urlopen(req, timeout=30) as resp:
        data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        return data


def get_filter_options(query: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Classify search results by product type via the configured LLM.

    Returns a dict ``{"options": [...], "assignments": [...]}`` where
    ``options`` is ``["Показать все"] + categories`` and ``assignments`` is a
    list (same length as ``results``) of the category label for each result
    index. Returns ``None`` when the LLM is not configured, the result list is
    empty, or the LLM call/parse fails — the caller then hides the filter.
    """
    llm = get_llm_config()
    endpoint = llm.get("endpoint", "").strip()
    api_key = llm.get("api_key", "").strip()
    model = llm.get("model", "").strip()

    if not endpoint or not results:
        return None

    titles = [r.get("title", "") for r in results]
    numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(titles))
    prompt = (
        f"Search query: {query}\n"
        f"Results (index: title):\n{numbered}\n\n"
        "Classify these results by product type.\n"
        "1. Suggest 3-5 short product-type category labels (Russian, max 30 chars each), "
        "e.g. «Только видеокарта», «Готовый ПК», «Оперативная память».\n"
        "2. Assign each result index to exactly one of your labels.\n\n"
        'Return ONLY a JSON object: {"categories": ["..."], "assignments": ["...", ...]}\n'
        f"where assignments has exactly {len(titles)} entries, one per result index.\n"
        "No explanation."
    )

    try:
        content = call_llm(endpoint, api_key, model, prompt)
        if content.startswith("{"):
            obj = json.loads(content)
        else:
            start = content.find("{")
            end = content.rfind("}") + 1
            obj = json.loads(content[start:end])

        categories = obj.get("categories", [])
        assignments = obj.get("assignments", [])
        if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
            return None
        if not isinstance(assignments, list) or len(assignments) != len(titles):
            return None

        categories = [c.strip() for c in dict.fromkeys(categories) if c.strip()][:5]
        if not categories:
            return None
        return {
            "options": ["Показать все"] + categories,
            "assignments": [str(a).strip() for a in assignments],
        }
    except Exception as exc:
        log.warning("LLM filter call failed: %s", exc)
        return None
