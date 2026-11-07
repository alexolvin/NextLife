#!/usr/bin/env python3
"""Close the Vinted draft and open the remaining account screens. No submit."""

from __future__ import annotations

import json
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

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-follow"


def _texts(xml: str) -> list[str]:
    found: list[str] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return found
    for node in root.iter("node"):
        raw = (node.get("text") or node.get("content-desc") or "").strip()
        item = " ".join(raw.split())
        if item and item not in found:
            found.append(item)
    return found[:60]


def _shot(transport, registry, platform: str, name: str) -> list[str]:
    xml = str(transport._op("ui_dump").get("xml") or "")
    registry.write_text(platform, RUN_ID, f"{name}.xml", xml)
    transport.screenshot(registry.run_dir(platform, RUN_ID) / f"{name}.png")
    return _texts(xml)


def _tap(transport, selector: str, notes: list[str]) -> None:
    try:
        transport._tap_selector(selector)
        notes.append(f"tapped {selector}")
        time.sleep(2)
    except Exception as exc:
        notes.append(f"fail {selector}: {type(exc).__name__}")


def _run(transport, registry, platform: str, body) -> dict:
    transport.lease_max_duration_s = 180
    notes: list[str] = []
    try:
        transport.begin(platform, "interactive")
    except Exception as exc:
        return {"platform": platform, "error": f"{type(exc).__name__}: {exc}"[:300]}
    try:
        transport.open_app()
        time.sleep(2)
        payload = body(transport, registry, notes)
        payload["platform"] = platform
        payload["notes"] = notes
        registry.write_json(platform, RUN_ID, "follow.json", payload)
        registry.finalize(platform, RUN_ID)
        return payload
    except Exception as exc:
        return {"platform": platform, "error": f"{type(exc).__name__}: {exc}"[:300], "notes": notes}
    finally:
        transport.end()


def _bazos(transport, registry, notes):
    _tap(transport, "resource-id=cz.ackee.bazos:id/bottom_nav_profile", notes)
    _tap(transport, "text=App settings", notes)
    return {"texts": _shot(transport, registry, "bazos_cz", "settings")}


def _sbazar(transport, registry, notes):
    _tap(transport, "text=Můj profil Karta 4 z 5", notes)
    return {"texts": _shot(transport, registry, "sbazar_cz", "profile")}


def _olx(transport, registry, notes):
    _tap(transport, "text=Konto", notes)
    return {"texts": _shot(transport, registry, "olx_pl", "account")}


def _vinted(transport, registry, notes):
    texts = _shot(transport, registry, "vinted", "open")
    if any("Nabídnout předmět" in item or "Kategorie" in item for item in texts):
        notes.append("sell form still open")
    else:
        _tap(transport, "text=Prodávat", notes)
        texts = _shot(transport, registry, "vinted", "sell")
    _tap(transport, "text=Kategorie", notes)
    cat = _shot(transport, registry, "vinted", "category")
    _tap(transport, "text=Zavřít", notes)
    for label in ("Zahodit", "Zrušit", "Leave", "Discard", "Smazat"):
        if label in " ".join(cat):
            _tap(transport, f"text={label}", notes)
    closed = _shot(transport, registry, "vinted", "closed")
    return {"category_texts": cat, "closed_texts": closed[:20]}


def main() -> None:
    registry = EvidenceRegistry(ROOT / "evidence")
    transport = make_phone_broker_transport(load_config().app.android_bridge.phone_broker)
    for name, fn in (("bazos_cz", _bazos), ("sbazar_cz", _sbazar), ("olx_pl", _olx), ("vinted", _vinted)):
        print("follow", name, flush=True)
        row = _run(transport, registry, name, fn)
        brief = {k: row.get(k) for k in ("platform", "error", "notes")}
        print(json.dumps(brief, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
