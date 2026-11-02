"""Provider-specific callers for Image AI (OpenAI, Gemini, Cloudflare Workers AI).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("nextlife.media.ai_providers")


class ImageAIError(RuntimeError):
    """Raised when Image AI request or parsing fails."""


def _check_gemini_status(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise ImageAIError(
            "Превышен лимит запросов Image AI (Google Gemini 429 Resource Exhausted / Rate Limit). "
            "На бесплатном тарифе Google AI Studio действует ограничение (2-5 запросов/мин). "
            "Подождите 1-2 мин или подключите биллинг в Google Cloud (с $300 бонусом) для снятия лимита."
        )
    if resp.status_code != 200:
        raise ImageAIError(f"Image AI (Gemini) вернул ошибку {resp.status_code}: {resp.text[:300]}")


def call_gemini_predict(target_model: str, prompt: str, base_url: str, api_key: str, endpoint: str) -> bytes:
    """Call Google Gemini Imagen predict endpoint."""
    u = endpoint if (endpoint and ":predict" in endpoint) else f"{base_url}/v1beta/models/{target_model}:predict"
    if api_key and "key=" not in u:
        u = f"{u}{'&' if '?' in u else '?'}key={api_key}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-goog-api-key"] = api_key
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "1:1",
            "outputOptions": {"mimeType": "image/jpeg"},
            "personGeneration": "ALLOW_ADULT",
            "safetySetting": "BLOCK_MEDIUM_AND_ABOVE",
        },
    }
    resp: httpx.Response | None = None
    for attempt in range(3):
        resp = httpx.post(u, headers=headers, json=payload, timeout=60.0)
        if resp.status_code == 429 and attempt < 2:
            time.sleep(2.5 * (attempt + 1))
            continue
        _check_gemini_status(resp)
        break
    assert resp is not None
    preds = resp.json().get("predictions", [])
    b64_str = (preds[0].get("bytesBase64Encoded") or preds[0].get("b64_json")) if preds and isinstance(preds, list) else None
    if b64_str:
        return base64.b64decode(b64_str)
    raise ValueError("bytesBase64Encoded не найден в ответе Image AI (Gemini)")


def call_gemini_content(target_model: str, prompt: str, image_bytes: bytes, base_url: str, api_key: str, endpoint: str) -> bytes:
    """Call Google Gemini native generateContent multimodal image endpoint."""
    u = endpoint if (endpoint and ":generateContent" in endpoint) else f"{base_url}/v1beta/models/{target_model}:generateContent"
    if api_key and "key=" not in u:
        u = f"{u}{'&' if '?' in u else '?'}key={api_key}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-goog-api-key"] = api_key
    parts: list[dict[str, Any]] = []
    if image_bytes:
        parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image_bytes).decode("ascii")}})
    parts.append({"text": prompt})
    payload = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}}
    resp: httpx.Response | None = None
    for attempt in range(3):
        resp = httpx.post(u, headers=headers, json=payload, timeout=60.0)
        if resp.status_code == 429 and attempt < 2:
            time.sleep(2.5 * (attempt + 1))
            continue
        _check_gemini_status(resp)
        break
    assert resp is not None
    for cand in resp.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise ValueError("Изображение не найдено в ответе Image AI (Gemini)")


def call_cloudflare_image(prompt: str, image_bytes: bytes, cfg: dict[str, str], model: str | None = None) -> bytes:
    """Call Cloudflare Workers AI image run endpoint."""
    endpoint = (cfg.get("endpoint") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    target_model = (model or cfg.get("model") or "@cf/runwayml/stable-diffusion-v1-5-inpainting").strip()

    if not endpoint:
        endpoint = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/"

    url = endpoint.rstrip("/")
    if not url.endswith(target_model):
        url = f"{url}/{target_model}"

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "prompt": prompt,
        "image_b64": base64.b64encode(image_bytes).decode("ascii") if image_bytes else "",
    }

    resp: httpx.Response | None = None
    for attempt in range(3):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        except Exception as exc:
            log.error("Cloudflare Workers AI request failed: %s", exc)
            raise ImageAIError(f"Ошибка соединения с Cloudflare Workers AI: {exc}") from exc

        if resp.status_code == 429 and attempt < 2:
            time.sleep(2.5 * (attempt + 1))
            continue
        break

    assert resp is not None
    if resp.status_code == 429:
        raise ImageAIError("Превышен лимит запросов Cloudflare Workers AI (HTTP 429 Rate Limit).")
    if resp.status_code != 200:
        raise ImageAIError(f"Cloudflare Workers AI вернул ошибку {resp.status_code}: {resp.text[:300]}")

    c_type = resp.headers.get("content-type", "")
    if c_type.startswith("image/") or resp.content.startswith((b"\x89PNG", b"\xff\xd8")):
        return resp.content

    try:
        data = resp.json()
        b64_val = data.get("result", {}).get("image") or data.get("result") or data.get("image") or data.get("b64_json")
        if isinstance(b64_val, str):
            return base64.b64decode(b64_val)
    except Exception as exc:
        raise ImageAIError(f"Не удалось распознать ответ Cloudflare Workers AI: {exc}") from exc

    return resp.content


def call_openai_image(prompt: str, image_bytes: bytes, cfg: dict[str, str], model: str | None = None) -> bytes:
    """Call OpenAI-compatible /v1/images/edits endpoint."""
    endpoint = cfg.get("endpoint", "").strip()
    api_key = cfg.get("api_key", "").strip()
    target_model = (model or cfg.get("model") or "dall-e-2").strip()

    if not endpoint:
        raise ImageAIError("Image AI EndPoint не настроен. Настройте Image AI в Настройках.")

    url = endpoint.rstrip("/")
    if not url.endswith("/images/edits"):
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        url = f"{url}/images/edits"

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    files = {"image": ("image.png", image_bytes, "image/png")}
    data = {"prompt": prompt, "model": target_model, "response_format": "b64_json", "n": 1}

    try:
        response = httpx.post(url, headers=headers, data=data, files=files, timeout=60.0)
    except Exception as exc:
        log.error("Image AI request failed: %s", exc)
        raise ImageAIError(f"Ошибка соединения с Image AI: {exc}") from exc

    if response.status_code == 429:
        raise ImageAIError("Image AI: Превышен лимит запросов к эндпоинту (HTTP 429 Rate Limit / Quota Exceeded).")
    if response.status_code != 200:
        err_msg = response.text[:300]
        log.error("Image AI returned status %s: %s", response.status_code, err_msg)
        raise ImageAIError(f"Image AI вернул ошибку {response.status_code}: {err_msg}")

    try:
        res_json = response.json()
        item_data = res_json.get("data", [])[0]
        if "b64_json" in item_data:
            return base64.b64decode(item_data["b64_json"])
        if "url" in item_data:
            img_res = httpx.get(item_data["url"], timeout=30.0)
            img_res.raise_for_status()
            return img_res.content
        raise ValueError("Ни b64_json, ни url не найдены в ответе Image AI")
    except Exception as exc:
        log.error("Failed to parse Image AI response: %s", exc)
        raise ImageAIError(f"Не удалось обработать ответ Image AI: {exc}") from exc
