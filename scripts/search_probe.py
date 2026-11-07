#!/usr/bin/env python3
"""Open three search hits for «RX 7900 XTX» and store them in the evidence registry."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nextlife.connectors.registry import get_connector  # noqa: E402
from nextlife.core.config import load_config  # noqa: E402
from nextlife.verify.evidence import EvidenceRegistry  # noqa: E402

QUERY = "RX 7900 XTX"
PLATFORMS = (
    "bazos_cz",
    "bazos_sk",
    "sbazar_cz",
    "bazar_cz",
    "aukro_cz",
    "vinted",
    "facebook_marketplace",
    "mimibazar_cz",
    "trhknih_cz",
    "kleinanzeigen_de",
    "willhaben_at",
    "olx_pl",
)
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _matches(text: str) -> bool:
    folded = (text or "").casefold()
    return "7900" in folded and "xtx" in folded


def _page_title(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    heading = soup.find("h1")
    if heading:
        return heading.get_text(" ", strip=True)
    if soup.title and soup.title.string:
        return soup.title.get_text(" ", strip=True)
    return ""


def _open_links(client: httpx.Client, rows: list[tuple[str, str]]) -> list[dict]:
    opened = []
    for url, connector_title in rows[:3]:
        if not url.startswith("http"):
            opened.append({
                "url": url,
                "http_status": 0,
                "matches_query": False,
                "connector_title": connector_title,
                "page_title": "",
            })
            continue
        try:
            response = client.get(url, follow_redirects=True, timeout=25)
            title = _page_title(response.text)
            opened.append({
                "url": str(response.url),
                "http_status": response.status_code,
                "matches_query": _matches(title) or _matches(connector_title),
                "connector_title": connector_title,
                "page_title": title[:180],
            })
        except httpx.HTTPError as exc:
            opened.append({
                "url": url,
                "http_status": 0,
                "matches_query": False,
                "connector_title": connector_title,
                "page_title": "",
                "error": type(exc).__name__,
            })
    return opened


def _search_one(config, client: httpx.Client, platform: str) -> dict:
    error = ""
    pairs: list[tuple[str, str]] = []
    try:
        connector = get_connector(platform, config, client)
        if platform == "facebook_marketplace" and hasattr(connector, "probe_search"):
            probe = connector.probe_search(QUERY)
            error = probe.network_error or ""
            if probe.requested_url:
                pairs = [(probe.requested_url, "")]
        else:
            rows = connector.search(QUERY)
            pairs = [
                (row.external_url, row.title or "")
                for row in rows
                if getattr(row, "external_url", "")
            ]
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    links = _open_links(client, pairs)
    if error.startswith("login_wall"):
        for link in links:
            link["matches_query"] = False
    if not links and not error:
        error = "поиск вернул 0 ссылок"
    return {"platform": platform, "query": QUERY, "links": links, "error": error}


def main() -> None:
    registry = EvidenceRegistry(ROOT / "evidence")
    config = load_config()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NextLifeSearch/1.0)"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=25) as client:
        for platform in PLATFORMS:
            print(f"search {platform}", flush=True)
            payload = _search_one(config, client, platform)
            registry.write_json("search", f"{platform}/{RUN_ID}", "result.json", payload)
            registry.finalize("search", f"{platform}/{RUN_ID}")
            print(json.dumps({
                "platform": platform,
                "links": len(payload["links"]),
                "error": payload["error"][:180],
            }, ensure_ascii=False), flush=True)
    print(json.dumps({"run_id": RUN_ID}))


if __name__ == "__main__":
    main()
