"""Thin HTTP client for the NextLife API (item 3).

The CLI reads ONLY two environment variables:
- NEXTLIFE_BASE_URL (default: loopback on port 8081, see DEFAULT_BASE_URL)
- NEXTLIFE_OPENCLAW_TOKEN (process env only; no .env fallback)

The token must be present BEFORE any network call: a missing token is a
client-side fail (exit 2 with the variable named), never a network error.
"""

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8081"
DEFAULT_TIMEOUT_SECONDS = 120.0


class CliTokenMissingError(Exception):
    """NEXTLIFE_OPENCLAW_TOKEN is absent from the process environment."""


class ApiError(Exception):
    """The API answered with an error status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def base_url() -> str:
    return os.environ.get("NEXTLIFE_BASE_URL", "").strip() or DEFAULT_BASE_URL


def get_token() -> str:
    """Read the token from the process env (no .env fallback). Fail fast."""
    token = os.environ.get("NEXTLIFE_OPENCLAW_TOKEN", "").strip()
    if not token:
        raise CliTokenMissingError(
            "NEXTLIFE_OPENCLAW_TOKEN is not set in the environment. "
            "The token is injected by the OpenClaw skill runtime; the CLI "
            "refuses to make any network call without it."
        )
    return token


def request(
    method: str,
    path: str,
    *,
    token: str,
    json_body: dict[str, Any] | None = None,
    files: list[tuple[str, tuple[str, Any]]] | None = None,
    form: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    """Perform one API call. Returns (status_code, parsed JSON body).

    Raises ApiError on 4xx/5xx (body parsed when possible) and
    httpx.TransportError on connection failure.
    """
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url(), headers=headers, timeout=timeout) as client:
        response = client.request(method, path, json=json_body, files=files, data=form)
    try:
        body: dict[str, Any] = response.json() if response.content else {}
    except ValueError:
        body = {"detail": response.text[:500]}
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise ApiError(response.status_code, str(detail) if detail else response.text[:500])
    return response.status_code, body
