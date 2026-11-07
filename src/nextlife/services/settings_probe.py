"""Connection probes for external service endpoints (LLM, Image AI, Notion).

Used by settings test buttons to verify reachability and authentication.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("nextlife.settings_probe")


def probe_llm_connection(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Verify Text LLM endpoint reachability and credentials.

    Returns (is_ok, notice_or_error_message).
    """
    provider = (cfg.get("provider") or "openai").strip().lower()
    endpoint = (cfg.get("endpoint") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip()

    if provider == "gemini" and not endpoint and not api_key:
        return False, "API Key или URL эндпоинта Gemini Text LLM не задан"
    if provider != "gemini" and not endpoint:
        return False, "URL эндпоинта LLM не задан"

    try:
        if provider == "gemini" or "generativelanguage.googleapis.com" in endpoint:
            m = model or "gemini-1.5-flash"
            url = endpoint
            if not url:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
            elif ":generateContent" not in url:
                base = url.rstrip("/")
                url = f"{base}:generateContent" if "/models/" in base else f"{base}/v1beta/models/{m}:generateContent"
            if api_key and "key=" not in url:
                url = f"{url}{'&' if '?' in url else '?'}key={api_key}"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["x-goog-api-key"] = api_key
            payload = {
                "contents": [{"parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 3},
            }
            resp = httpx.post(url, headers=headers, json=payload, timeout=4.0)
            if resp.status_code == 200:
                return True, "llm_test_ok"
            err_msg = f"Gemini API вернул статус {resp.status_code}"
            try:
                data = resp.json()
                if "error" in data and "message" in data["error"]:
                    err_msg += f": {data['error']['message']}"
            except Exception:
                pass
            return False, err_msg
        else:
            url = endpoint.rstrip("/")
            if not url.endswith("/chat/completions"):
                url = url + "/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
            }
            resp = httpx.post(url, headers=headers, json=payload, timeout=4.0)
            if resp.status_code in (200, 201):
                return True, "llm_test_ok"
            if resp.status_code in (401, 403):
                return False, f"Ошибка авторизации LLM (HTTP {resp.status_code}): проверьте API Key"
            if resp.status_code == 404:
                return False, f"Эндпоинт LLM не найден (HTTP 404): проверьте URL {url}"
            return False, f"Сервер LLM вернул статус {resp.status_code}: {resp.text[:120]}"
    except httpx.ConnectTimeout:
        return False, "Сервер LLM не отвечает: таймаут соединения (сервер выключен или недоступен)"
    except httpx.ReadTimeout:
        return False, "Сервер LLM не ответил вовремя: таймаут ожидания ответа"
    except (httpx.ConnectError, httpx.NetworkError):
        return False, "Сервер LLM недоступен: соединение отклонено (хост выключен или порт недоступен)"
    except Exception as exc:
        return False, f"Ошибка подключения к LLM: {exc}"


def probe_image_ai_connection(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Verify Image AI endpoint reachability and credentials.

    Returns (is_ok, notice_or_error_message).
    """
    provider = (cfg.get("provider") or "openai").strip().lower()
    endpoint = (cfg.get("endpoint") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip()

    if provider == "gemini" and not endpoint and not api_key:
        return False, "API Key или URL эндпоинта Gemini Image AI не задан"
    if provider == "cloudflare" and not api_key:
        return False, "API Token для Cloudflare Workers AI не задан"
    if provider not in ("gemini", "cloudflare") and not endpoint:
        return False, "URL эндпоинта Image AI не задан"

    try:
        if provider == "cloudflare":
            resp = httpx.get(
                "https://api.cloudflare.com/client/v4/user/tokens/verify",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=4.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return True, "image_ai_test_ok"
                return False, "Cloudflare API Token недействителен"
            return False, f"Cloudflare API вернул статус {resp.status_code}"
        elif provider == "gemini":
            u = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}" if api_key else f"{endpoint.rstrip('/')}/v1beta/models"
            resp = httpx.get(u, timeout=4.0)
            if resp.status_code == 200:
                return True, "image_ai_test_ok"
            return False, f"Gemini Image AI вернул статус {resp.status_code}"
        else:
            url = endpoint.rstrip("/")
            probe_url = f"{url}/models" if not url.endswith("/models") else url
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = httpx.get(probe_url, headers=headers, timeout=4.0)
            if resp.status_code in (200, 201):
                return True, "image_ai_test_ok"
            return False, f"Image AI вернул статус {resp.status_code}"
    except httpx.ConnectTimeout:
        return False, "Сервер Image AI не отвечает: таймаут соединения"
    except (httpx.ConnectError, httpx.NetworkError):
        return False, "Сервер Image AI недоступен: соединение отклонено"
    except Exception as exc:
        return False, f"Ошибка подключения к Image AI: {exc}"
