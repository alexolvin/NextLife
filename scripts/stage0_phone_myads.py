#!/usr/bin/env python3
"""Second read-only pass: open My ads / the post gate. Does not submit a form."""

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

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-myads"

# One screen each. Selectors come from the previous ui dump, not from coordinates.
STEPS = {
    "bazos_cz": ["resource-id=cz.ackee.bazos:id/bottom_nav_profile"],
    "sbazar_cz": ["text=Můj profil"],
    "vinted": ["text=Profil", "text=Zobrazit moje nabídky"],
    "kleinanzeigen_de": ["text=Inserieren"],
    "olx_pl": ["resource-id=com.android.permissioncontroller:id/permission_deny_and_dont_ask_again_button"],
    "willhaben_at": ["text=Verkaufen"],
}


def _texts(xml: str) -> list[str]:
    found: list[str] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return found
    for node in root.iter("node"):
        if node.get("password") == "true":
            continue
        for raw in (node.get("text") or "", node.get("content-desc") or ""):
            item = " ".join(raw.split())
            if item and item not in found:
                found.append(item)
    return found[:80]


def _one(transport, registry: EvidenceRegistry, platform: str) -> dict:
    transport.lease_max_duration_s = 180
    notes: list[str] = []
    try:
        transport.begin(platform, "interactive")
    except Exception as exc:
        return {"platform": platform, "error": f"{type(exc).__name__}: {exc}"[:400]}
    try:
        transport.open_app()
        time.sleep(2)
        for index, selector in enumerate(STEPS[platform], start=1):
            try:
                transport._tap_selector(selector)
                notes.append(f"tapped {selector}")
            except Exception as exc:
                notes.append(f"tap failed {selector}: {type(exc).__name__}")
            time.sleep(2)
            xml = str(transport._op("ui_dump").get("xml") or "")
            registry.write_text(platform, RUN_ID, f"step{index}.xml", xml)
            transport.screenshot(registry.run_dir(platform, RUN_ID) / f"step{index}.png")
            texts = _texts(xml)
        else:
            texts = texts if STEPS[platform] else []
        if platform == "vinted" and any(item == "Zobrazit moje nabídky" for item in texts):
            # The profile row was not the screen we landed on; try the offers row.
            pass
        payload = {
            "platform": platform,
            "texts": texts,
            "notes": notes,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if platform == "vinted":
            _vinted_sell(transport, registry, payload)
        registry.write_json(platform, RUN_ID, "myads.json", payload)
        registry.finalize(platform, RUN_ID)
        return payload
    except Exception as exc:
        return {"platform": platform, "error": f"{type(exc).__name__}: {exc}"[:400], "notes": notes}
    finally:
        transport.end()


def _vinted_sell(transport, registry: EvidenceRegistry, payload: dict) -> None:
    """Open the sell tab far enough to see categories. Do not fill or send."""
    try:
        transport._op("keyevent", {"keycode": 4})
        time.sleep(1)
        transport._tap_selector("text=Prodávat")
        time.sleep(2)
        xml = str(transport._op("ui_dump").get("xml") or "")
        registry.write_text("vinted", RUN_ID, "sell.xml", xml)
        transport.screenshot(registry.run_dir("vinted", RUN_ID) / "sell.png")
        payload["sell_texts"] = _texts(xml)
        payload["notes"].append("opened Prodávat")
    except Exception as exc:
        payload["notes"].append(f"sell: {type(exc).__name__}: {exc}"[:240])


def main() -> None:
    registry = EvidenceRegistry(ROOT / "evidence")
    transport = make_phone_broker_transport(load_config().app.android_bridge.phone_broker)
    rows = []
    for platform in STEPS:
        print(f"myads {platform}", flush=True)
        row = _one(transport, registry, platform)
        rows.append({
            "platform": platform,
            "error": row.get("error", ""),
            "notes": row.get("notes", []),
            "texts": (row.get("texts") or [])[:12],
        })
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    registry.write_json("stage0", RUN_ID, "myads_index.json", rows)
    registry.finalize("stage0", RUN_ID)


if __name__ == "__main__":
    main()
