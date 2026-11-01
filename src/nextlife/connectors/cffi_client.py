"""A curl_cffi-backed HTTP client speaking the httpx.Client interface.

Some marketplaces (e.g. annonce.cz behind Cloudflare) answer the default
httpx TLS fingerprint (JA3/JA4) with a 403 challenge. A real browser TLS
fingerprint (curl_cffi ``impersonate="chrome"``) passes the same request
(verified 2026-10-18: annonce.cz homepage + search both return 200). This
adapter lets the base connector keep using its httpx-style ``self.client``
(``.get`` -> ``.status_code``/``.text``/``.url``/``.raise_for_status``)
while the transport is curl_cffi, and maps curl_cffi network errors to
``httpx.TransportError`` so the base ``probe_search`` error handling works
unchanged.
"""

from typing import Any

import httpx
from curl_cffi import requests as cffi_requests


class CffiClient:
    """httpx.Client-compatible wrapper over a curl_cffi session."""

    def __init__(self, impersonate: str = "chrome", timeout: float = 20.0) -> None:
        self._session: Any = cffi_requests.Session(impersonate=impersonate, timeout=timeout)

    def get(self, url: str, **kwargs: object) -> Any:
        try:
            return self._session.get(url, **kwargs)
        except Exception as exc:  # curl_cffi network/transport errors
            raise httpx.TransportError(str(exc)) from exc
