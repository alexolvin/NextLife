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
import re
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


def classify_results_rule_based(query: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fallback rule-based product-type classifier when LLM is unavailable."""
    if not results:
        return None

    q_lower = (query or "").lower().strip()
    query_words = set(re.findall(r"\w+", q_lower))

    # 1. Video Cards / PC Components
    gpu_terms = (
        "rtx", "gtx", "radeon", "geforce", "gpu", "видеокарт", "grafická", "graficka", "xtx"
    )
    gpu_code_words = (
        "rx", "xt", "gre", "ti", "super",
        "7900", "7800", "7700", "7600", "6950", "6900", "6800", "6700", "6650", "6600", "5700", "5600",
        "4090", "4080", "4070", "4060", "3090", "3080", "3070", "3060", "2080", "2070", "2060",
        "1080", "1070", "1060", "1660"
    )
    is_gpu = any(t in q_lower for t in gpu_terms) or any(w in query_words for w in gpu_code_words)
    if not is_gpu:
        gpu_matches = sum(
            1 for r in results
            if any(k in (r.get("title") or "").lower() for k in ("grafick", "radeon", "geforce", "rtx", "gtx", "xtx", "vram"))
        )
        if gpu_matches >= max(1, len(results) // 4):
            is_gpu = True

    # 2. Sofas / Furniture
    sofa_terms = ("диван", "pohovka", "sedačka", "sedacka", "gauč", "gauc", "диван-кровать")
    is_sofa = any(t in q_lower for t in sofa_terms)

    demand_re = re.compile(
        r"\b(koupím|koupim|sháním|shanim|shánim|hledám|hledam|купить|куплю|vyměním|vymenim|обмен|poptávka|poptavka|poptávám|poptavam|výkup|vykup|odkoupím|odkoupim)\b",
        re.IGNORECASE,
    )

    pc_build_keywords = (
        "herní pc", "herni pc", "herní počítač", "herni pocitac", "herní sestava", "herni sestava",
        "pc sestava", "gaming pc", "desktop", "stolní pc", "stolni pc", "компьютер",
        "сборка", "системный блок", "celé pc", "cele pc", "herný počítač", "herny pocitac",
        "celá sestava", "cela sestava", "workstation", "mining rig", "herní komp", "herni komp",
    )

    pc_slang_re = re.compile(
        r"\b(hern[íi]\s+(bestie|dělo|delo|mašin[ayieou]|masin[ayieou]|stroj|monstrum|raket[ayieou])|"
        r"výkonn[ýé]\s+(herní\s+)?(stroj|mašina|masina|dělo|delo|bestie)|"
        r"počítačov[áé]|počítač|pocitac)\b",
        re.IGNORECASE,
    )

    kit_re = re.compile(
        r"\b(комплект|комплекты|набор|наборы|бандл|в сборе|sada|sady|bundle|balík|balik|komplet|kit|lot|balení|baleni|souprava|гарнитур)\b",
        re.IGNORECASE,
    )

    cooling_keywords = (
        "chlazení", "chlazeni", "waterblock", "vodní blok", "vodni blok", "blok",
        "ventilátor", "ventilator", "cooler", "кулер", "охлаждение", "backplate",
        "držák", "drzak", "riser"
    )

    accessory_keywords = (
        "krabice", "jen krabice", "pouze krabice", "obal", "kryt", "чехол", "аксессуары",
        "kabel", "adaptér", "adapter", "pouzdro", "stojánek", "stojanek"
    )

    sofa_folding_kw = ("rozkládací", "rozkladaci", "раскладн", "спальн")
    sofa_leather_kw = ("kožená", "kozena", "kůže", "kuze", "кож", "leather")
    sofa_corner_kw = ("rohová", "rohova", "углов")
    sofa_ikea_kw = ("ikea", "икеа")

    assignments: list[str] = []

    for r in results:
        title_text = r.get("title") or ""
        url_text = (r.get("url") or r.get("external_url") or "").replace("-", " ").replace("/", " ").replace(".php", "").replace(".html", "")
        t = f"{title_text} {url_text}".lower()

        # 1. Demand / Wanted check
        if demand_re.search(t):
            assignments.append("Спрос / Покупка")
            continue

        # 2. Builds / Kits / Bundles check
        is_build = any(k in t for k in pc_build_keywords) or bool(pc_slang_re.search(t))
        if not is_build and re.search(r"\bsestav[ayieou]?\b", t):
            is_build = True
        if not is_build and is_gpu:
            if re.search(
                r"\b(ryzen|intel core|i[3579][-\s]\d{4,5}|[579]\d00x3d|1[234]\d00k[f]?|"
                r"\d+\s*gb\s+(?:ram|ddr[45])|ddr[45]|nvme|m\.2\s+ssd|"
                r"základní deska|zakladni deska|motherboard)\b",
                t,
                re.IGNORECASE,
            ):
                is_build = True

        is_kit = is_build or bool(kit_re.search(t))
        if not is_kit and re.search(r"(?<!nitro)(?<!oc)\+\s*(monitor|hry|hra|ovladač|ovladac|zdroj|case|ram|cpu|deska|ssd|chladič|chladic|myš|mys|kláves|klaves)", t):
            is_kit = True

        if is_kit:
            assignments.append("Наборы, комплекты")
            continue

        # 3. Cooling / Components / Accessories check
        if any(k in t for k in cooling_keywords):
            assignments.append("Комплектующие / Охлаждение")
            continue
        if any(k in t for k in accessory_keywords):
            assignments.append("Аксессуары")
            continue

        # 4. Sofa-specific breakdown if applicable
        if is_sofa:
            if any(k in t for k in sofa_ikea_kw):
                assignments.append("IKEA")
            elif any(k in t for k in sofa_folding_kw):
                assignments.append("Раскладные")
            elif any(k in t for k in sofa_corner_kw):
                assignments.append("Угловые")
            elif any(k in t for k in sofa_leather_kw):
                assignments.append("Кожаные")
            else:
                assignments.append("Самостоятельный товар")
            continue

        # 5. Default standalone item
        assignments.append("Самостоятельный товар")

    unique_cats = [c for c in dict.fromkeys(assignments) if c]
    if len(unique_cats) < 2:
        return None

    preferred_order = [
        "Самостоятельный товар",
        "Наборы, комплекты",
        "Спрос / Покупка",
        "Комплектующие / Охлаждение",
        "Аксессуары",
        "IKEA",
        "Раскладные",
        "Угловые",
        "Кожаные",
    ]
    sorted_cats = [c for c in preferred_order if c in unique_cats] + [c for c in unique_cats if c not in preferred_order]

    return {
        "options": ["Показать все"] + sorted_cats,
        "assignments": assignments,
    }


def get_filter_options(query: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Classify search results by product type via the configured LLM.

    Returns a dict ``{"options": [...], "assignments": [...]}`` where
    ``options`` is ``["Показать все"] + categories`` and ``assignments`` is a
    list (same length as ``results``) of the category label for each result
    index. When LLM is not configured or fails, falls back to rule-based classification.
    """
    if not results:
        return None

    llm = get_llm_config()
    endpoint = llm.get("endpoint", "").strip()
    api_key = llm.get("api_key", "").strip()
    model = llm.get("model", "").strip()

    if endpoint:
        titles = [r.get("title", "") for r in results]
        numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(titles))
        prompt = (
            f"Search query: {query}\n"
            f"Results (index: title):\n{numbered}\n\n"
            "Classify these classifieds results by product type into Russian categories:\n"
            "- «Самостоятельный товар» (standalone single item e.g. GPU card, smartphone, console);\n"
            "- «Наборы, комплекты» (full PC builds, assemblies, sets, bundles, lots);\n"
            "- «Спрос / Покупка» (buy / wanted requests: koupím, sháním, poptávka, куплю);\n"
            "- «Комплектующие / Аксессуары» (cooling, waterblocks, cables, cases, parts).\n"
            "Assign each result index to exactly one of these labels.\n\n"
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
            if (
                isinstance(categories, list)
                and all(isinstance(c, str) for c in categories)
                and isinstance(assignments, list)
                and len(assignments) == len(titles)
            ):
                categories = [c.strip() for c in dict.fromkeys(categories) if c.strip()][:5]
                if categories:
                    return {
                        "options": ["Показать все"] + categories,
                        "assignments": [str(a).strip() for a in assignments],
                    }
        except Exception as exc:
            log.warning("LLM filter call failed: %s", exc)

    return classify_results_rule_based(query, results)
