"""Settings and secrets storage service for NextLife (TZ §11).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextlife.core.config import get_data_dir


def _settings_path() -> Path:
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


SECRET_KEYS = (
    "BAZOS_PHONE", "SELLER_NAME", "SELLER_PSC", "BAZOS_PASSWORD",
    "SBAZAR_EMAIL", "SBAZAR_PASSWORD", "ALLEGRO_CLIENT_ID", "ALLEGRO_CLIENT_SECRET",
)

MP_SECRETS: dict[str, list[tuple[str, str, str, str]]] = {
    "bazos_cz": [
        ("BAZOS_PHONE", "Телефон (SMS-подтверждение)", "text", "+420..."),
        ("BAZOS_PASSWORD", "Пароль аккаунта Bazoš", "password", "••••••"),
    ],
    "bazos_sk": [
        ("BAZOS_PHONE", "Телефон (SMS-подтверждение)", "text", "+421..."),
        ("BAZOS_PASSWORD", "Пароль аккаунта Bazoš SK", "password", "••••••"),
    ],
    "sbazar_cz": [
        ("SBAZAR_EMAIL", "Email аккаунта Seznam", "text", "user@seznam.cz"),
        ("SBAZAR_PASSWORD", "Пароль аккаунта Seznam", "password", "••••••"),
    ],
    "allegro_cz": [
        ("ALLEGRO_CLIENT_ID", "Client ID", "text", "allegro_client_id"),
        ("ALLEGRO_CLIENT_SECRET", "Client Secret", "password", "••••••"),
    ],
}

SELLER_SECRETS: list[tuple[str, str, str, str]] = [
    ("SELLER_NAME", "Имя / название", "text", "имя"),
    ("SELLER_PSC", "PSC / адрес", "text", "PSC / адрес"),
]


def get_secrets() -> dict[str, str]:
    secrets = load_settings().get("secrets", {})
    if not isinstance(secrets, dict):
        return {}
    return {str(k): str(v) for k, v in secrets.items()}
