#!/usr/bin/env python3
"""Stage 0 phone inventory. Reads screens. Does not submit a listing form."""

from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nextlife.android.transport import make_phone_broker_transport  # noqa: E402
from nextlife.core.config import load_config  # noqa: E402
from nextlife.verify.evidence import EvidenceRegistry  # noqa: E402

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
PLATFORMS = (
    "bazos_cz",
    "sbazar_cz",
    "vinted",
    "kleinanzeigen_de",
    "willhaben_at",
    "olx_pl",
)
_MY_ADS = (
    "My ads",
    "Moje inzeráty",
    "Moje inzeraty",
    "Moje inzeráty",
    "Mé inzeráty",
    "Moje",
    "Inzeráty",
    "Meine Anzeigen",
    "My listings",
    "Ogłoszenia",
    "Moje ogłoszenia",
    "Meine",
    "Profil",
)


def _redact(text: str) -> str:
    return re.sub(r"\+?\d[\d \t]{6,}\d", "[redacted]", text or "")


def _nodes(xml: str) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    ids: list[str] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return texts, ids
    for node in root.iter("node"):
        if node.get("password") == "true":
            continue
        for raw in (node.get("text") or "", node.get("content-desc") or ""):
            item = raw.strip()
            if item and item not in texts:
                texts.append(_redact(item))
        rid = node.get("resource-id") or ""
        if rid and rid not in ids:
            ids.append(rid)
    return texts[:100], ids[:120]


def _post_lease(transport, platform: str, priority: str, seconds: int) -> tuple[int, dict]:
    return transport._req(
        "POST",
        "/lease",
        {
            "platform": platform,
            "operation": "tz-connectors-read",
            "priority": priority,
            "max_duration_s": seconds,
        },
    )


def _propertyboss_lease(report: list[dict]) -> None:
    env_path = Path.home() / "myhub" / "PropertyBoss" / ".env"
    token = ""
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("PHONEBROKER_TOKEN_PROPERTYBOSS="):
                token = line.split("=", 1)[1].strip().strip('"')
                break
    if not token:
        report.append({"platform": "propertyboss", "http": 0, "detail": "token missing"})
        return
    import urllib.error
    import urllib.request

    body = json.dumps(
        {
            "platform": "sreality_cz",
            "operation": "tz-connectors-lease-check",
            "priority": "background",
            "max_duration_s": 60,
        }
    ).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8090/lease",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read() or b"{}")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"detail": "non-json"}
        status = exc.code
    lease_id = str(payload.get("id") or "")
    closed = ""
    if lease_id:
        close = urllib.request.Request(
            f"http://127.0.0.1:8090/lease/{lease_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(close, timeout=30) as response:
                closed = str(response.status)
        except urllib.error.HTTPError as exc:
            closed = str(exc.code)
    detail = payload.get("detail") or payload.get("status") or ""
    report.append({
        "platform": "sreality_cz",
        "project": "propertyboss",
        "http": status,
        "lease_state": payload.get("status") or "",
        "closed": closed,
        "detail": str(detail)[:300],
    })


def _inventory_one(transport, registry: EvidenceRegistry, platform: str) -> dict:
    transport.lease_max_duration_s = 240
    notes: list[str] = []
    try:
        transport.begin(platform, "background")
    except Exception as exc:
        return {"platform": platform, "error": type(exc).__name__ + ": " + str(exc)[:400]}
    try:
        transport.open_app()
        time.sleep(2)
        dump = transport._op("ui_dump")
        xml = str(dump.get("xml") or "")
        registry.write_text(platform, RUN_ID, "ui.xml", xml)
        shot = registry.run_dir(platform, RUN_ID) / "page.png"
        transport.screenshot(shot)
        texts, ids = _nodes(xml)
        opened = ""
        for label in _MY_ADS:
            if label in texts or label in xml:
                try:
                    transport._tap_selector(f"text={label}")
                    time.sleep(2)
                    opened = label
                    break
                except Exception as exc:
                    notes.append(f"tap {label}: {type(exc).__name__}")
        if opened:
            dump2 = transport._op("ui_dump")
            xml2 = str(dump2.get("xml") or "")
            registry.write_text(platform, RUN_ID, "ui-my-ads.xml", xml2)
            transport.screenshot(registry.run_dir(platform, RUN_ID) / "my-ads.png")
            texts2, ids2 = _nodes(xml2)
        else:
            texts2, ids2 = [], []
        search_urls: list[str] = []
        if platform == "vinted":
            search_urls = _vinted_search(transport, registry, notes)
        payload = {
            "platform": platform,
            "app_version": transport.app_version(),
            "texts": texts,
            "resource_ids": [item for item in ids if "nav" in item or "tab" in item or "button" in item][:40],
            "my_ads_label": opened,
            "my_ads_texts": texts2[:60],
            "my_ads_ids": ids2[:40],
            "search_urls": search_urls,
            "notes": notes,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        registry.write_json(platform, RUN_ID, "inventory.json", payload)
        registry.finalize(platform, RUN_ID)
        return payload
    except Exception as exc:
        return {"platform": platform, "error": type(exc).__name__ + ": " + str(exc)[:400], "notes": notes}
    finally:
        transport.end()


def _vinted_search(transport, registry: EvidenceRegistry, notes: list[str]) -> list[str]:
    market = load_config().marketplaces.get("vinted")
    android = getattr(market, "android", None) if market else None
    search = getattr(android, "search", None) if android else None
    if search is None or not search.search_field:
        notes.append("vinted search config missing")
        return []
    cfg = {
        "search_field": search.search_field,
        "result_card": search.result_card,
        "share_menu": search.share_menu,
        "share_action": search.share_action,
        "consent_selector": search.consent_selector,
        "domain": search.domain,
        "link_limit": "3",
    }
    try:
        xml = transport.search_in_app("RX 7900 XTX", cfg)
    except Exception as exc:
        notes.append(f"vinted search: {type(exc).__name__}: {exc}"[:300])
        return []
    registry.write_text("vinted", RUN_ID, "search.xml", xml)
    try:
        urls, _after = transport.extract_card_links(xml, cfg)
    except Exception as exc:
        notes.append(f"vinted links: {type(exc).__name__}: {exc}"[:300])
        return []
    return [url for url in urls if url][:3]


def main() -> None:
    registry = EvidenceRegistry(ROOT / "evidence")
    pb_report: list[dict] = []
    _propertyboss_lease(pb_report)
    registry.write_json("stage0", RUN_ID, "propertyboss_lease.json", pb_report)
    cfg = load_config().app.android_bridge.phone_broker
    transport = make_phone_broker_transport(cfg)
    summaries = []
    for platform in PLATFORMS:
        print(f"inventory {platform}", flush=True)
        summaries.append(_inventory_one(transport, registry, platform))
        print(json.dumps({"platform": platform, "error": summaries[-1].get("error", ""), "my_ads": summaries[-1].get("my_ads_label", "")}, ensure_ascii=False), flush=True)
    registry.write_json("stage0", RUN_ID, "phone_inventory_index.json", [
        {key: row.get(key) for key in ("platform", "error", "my_ads_label", "app_version")}
        for row in summaries
    ])
    vinted_urls = []
    for row in summaries:
        if row.get("platform") == "vinted":
            vinted_urls = [url for url in (row.get("search_urls") or []) if str(url).startswith("http")]
    if vinted_urls:
        import httpx

        links = []
        with httpx.Client(follow_redirects=True, timeout=25) as client:
            for url in vinted_urls[:3]:
                try:
                    response = client.get(url)
                    text = response.text.casefold()
                    links.append({
                        "url": str(response.url),
                        "http_status": response.status_code,
                        "matches_query": "7900" in text and "xtx" in text,
                    })
                except httpx.HTTPError as exc:
                    links.append({"url": url, "http_status": 0, "matches_query": False, "error": type(exc).__name__})
        registry.write_json("search", f"vinted-app/{RUN_ID}", "result.json", {
            "platform": "vinted",
            "links": links,
            "error": "",
        })
        registry.finalize("search", f"vinted-app/{RUN_ID}")
    registry.finalize("stage0", RUN_ID)
    print(json.dumps({"run_id": RUN_ID, "propertyboss": pb_report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
