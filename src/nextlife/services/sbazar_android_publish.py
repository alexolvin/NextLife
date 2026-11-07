"""SBazar.cz publish through the official Android app (PhoneBroker).

Uses the verified Seznam account profile on the phone to publish listings.
``dry_run=True`` fills the offer form, attaches photos, and leaves without publishing.
``dry_run=False`` submits the offer and records the live listing URL.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ElementTree
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nextlife.android.errors import AndroidBridgeError, BrokerMaintenanceError
from nextlife.android.transport import make_phone_broker_transport
from nextlife.core.config import get_data_dir, load_config
from nextlife.services.llm_filter import get_secrets

log = logging.getLogger("nextlife.sbazar_android")

Box = tuple[int, int, int, int]


def _bounds(b_str: str) -> Box | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (b_str or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def _center(box: Box) -> tuple[int, int]:
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


def _nodes(xml: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return nodes
    for node in root.iter("node"):
        b = _bounds(node.get("bounds", ""))
        nodes.append(
            {
                "cls": (node.get("class") or "").split(".")[-1],
                "text": (node.get("text") or "").strip(),
                "desc": (node.get("content-desc") or "").strip(),
                "resource_id": (node.get("resource-id") or "").strip(),
                "bounds": b,
                "click": node.get("clickable") == "true",
            }
        )
    return nodes


def _visible_texts(xml: str) -> list[str]:
    seen: list[str] = []
    for node in _nodes(xml):
        for raw in (node["text"], node["desc"]):
            item = (raw or "").strip()
            if item and item not in seen:
                seen.append(item)
    return seen


def _read_ui(transport: Any) -> str:
    res = transport._op("ui_dump")
    return res.get("xml", "")


def _tap_box(transport: Any, box: Box) -> None:
    x, y = _center(box)
    transport._op("tap", {"x": x, "y": y})


def _type_into(transport: Any, box: Box, value: str) -> None:
    _tap_box(transport, box)
    time.sleep(0.4)
    transport._op("clear_text")
    time.sleep(0.2)
    if value:
        transport._text(value)
        time.sleep(0.3)


def _key_back(transport: Any) -> None:
    transport._op("keyevent", {"keycode": 4})


def _photo_files(photo_paths: list[str] | None, item_code: str) -> list[Path]:
    files = [Path(p) for p in (photo_paths or []) if p]
    if not files and item_code:
        from sqlalchemy import select

        from nextlife.storage.db import get_db_session
        from nextlife.storage.models import ItemPhoto

        with get_db_session() as session:
            rows = session.scalars(
                select(ItemPhoto)
                .where(ItemPhoto.item_code == item_code, ItemPhoto.role == "PUBLISH")
                .order_by(ItemPhoto.sort_order)
            ).all()
            if not rows:
                rows = session.scalars(
                    select(ItemPhoto)
                    .where(ItemPhoto.item_code == item_code, ItemPhoto.role == "ORIGINAL")
                    .order_by(ItemPhoto.sort_order)
                ).all()
            files = [Path(r.path) for r in rows if r.path and Path(r.path).is_file()]
    return files


def sbazar_android_publish_listing(
    *,
    title: str,
    description: str,
    price: str,
    category: str = "Grafické karty",
    location: str = "Praha",
    seller_phone: str = "",
    dry_run: bool = False,
    item_code: str = "",
    photo_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Publish an ad on SBazar.cz via the official Android application."""
    steps: list[str] = []
    errors: list[str] = []

    # Clean title to <= 50 chars for SBazar limit
    short_title = title.strip()
    if len(short_title) > 50:
        short_title = short_title[:50].strip()

    clean_price = re.sub(r"\D", "", price or "")
    if not short_title or not description.strip() or not clean_price:
        return {
            "ok": False,
            "steps": steps,
            "errors": ["Не заполнены заголовок, описание или цена."],
            "evidence_path": "",
            "final_url": "",
            "dry_run": dry_run,
            "channel": "android",
            "item_code": item_code,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    files = _photo_files(photo_paths, item_code)
    secrets = get_secrets()
    phone = (seller_phone or secrets.get("SELLER_PHONE") or "").strip()
    # Normalize phone: extract last 9 digits without country code
    phone_digits = re.sub(r"\D", "", phone)
    if len(phone_digits) >= 9:
        phone_digits = phone_digits[-9:]

    cfg = load_config().app.android_bridge.phone_broker
    transport = make_phone_broker_transport(cfg)
    transport.lease_max_duration_s = 600
    evidence = ""
    submitted = False
    final_url = ""

    try:
        transport.begin("sbazar_cz", "interactive")
        transport.open_app()
        time.sleep(2.5)

        # 1. Push photos if available
        if files:
            transport.push_media([str(f) for f in files])
            steps.append(f"photos pushed: {len(files)}")
            time.sleep(1.0)

        # 2. Open Create Offer screen via Profile
        _tap_box(transport, (648, 2094, 864, 2265))  # Můj profil tab
        time.sleep(2.0)
        xml = _read_ui(transport)

        # Tap "Vytvořit nabídku" or fallback to bottom "Přidat" tab
        create_btn = next((n for n in _nodes(xml) if "Vytvořit nabídku" in (n.get("desc") or "") and n.get("bounds")), None)
        if create_btn:
            _tap_box(transport, create_btn["bounds"])
        else:
            _tap_box(transport, (432, 2089, 648, 2270))  # Přidat tab
        time.sleep(2.5)

        xml = _read_ui(transport)
        if not any("Co prodáváte" in (n.get("desc") or "") for n in _nodes(xml)):
            errors.append("Экран создания объявления SBazar не открылся.")
            return _make_result(False, steps, errors, evidence, dry_run, item_code, final_url)

        steps.append("create offer screen open")

        # 3. Fill Title (Co prodáváte?)
        title_node = next((n for n in _nodes(xml) if n["cls"] == "EditText" and n["bounds"]), None)
        if title_node:
            _type_into(transport, title_node["bounds"], short_title)
            steps.append(f"filled title: {short_title}")
            time.sleep(0.5)

        # 4. Attach Photos
        media_btn = next((n for n in _nodes(xml) if "Nahrát média" in (n.get("desc") or "") and n.get("bounds")), None)
        if media_btn and files:
            _tap_box(transport, media_btn["bounds"])
            time.sleep(2.0)
            picker_xml = _read_ui(transport)
            # Pick from gallery
            gal_opt = next((n for n in _nodes(picker_xml) if "galerie" in (n.get("desc") or n.get("text") or "").lower() and n.get("bounds")), None)
            if gal_opt:
                _tap_box(transport, gal_opt["bounds"])
                time.sleep(2.0)
                # Tapping files in documentsui or photopicker
                doc_xml = _read_ui(transport)
                tiles = [n["bounds"] for n in _nodes(doc_xml) if n.get("click") and n.get("bounds") and n["bounds"][1] > 300]
                tapped = 0
                for b in tiles[:len(files)]:
                    _tap_box(transport, b)
                    tapped += 1
                    time.sleep(0.3)
                # Confirm button (Select / Hotovo / Done)
                conf = next((n for n in _nodes(_read_ui(transport)) if any(w in (n.get("text") or n.get("desc") or "").lower() for w in ("vybrat", "select", "done", "hotovo", "otevřít")) and n.get("bounds")), None)
                if conf:
                    _tap_box(transport, conf["bounds"])
                    time.sleep(2.0)
                steps.append(f"photos attached: {tapped}")

        # 5. Fill Description (Popisek)
        desc_node = next((n for n in _nodes(_read_ui(transport)) if n["cls"] == "EditText" and n.get("bounds") and n["bounds"][1] > 700), None)
        if desc_node:
            _type_into(transport, desc_node["bounds"], description.strip()[:2000])
            steps.append("filled description")
            time.sleep(0.5)

        # 6. Fill Price (Kč)
        transport.scroll_down()
        time.sleep(1.0)
        xml = _read_ui(transport)
        price_node = next((n for n in _nodes(xml) if n["cls"] == "EditText" and n.get("bounds")), None)
        if price_node:
            _type_into(transport, price_node["bounds"], clean_price)
            steps.append(f"filled price: {clean_price} Kč")
            time.sleep(0.5)

        # 7. Fill Phone
        if phone_digits:
            min_y = price_node["bounds"][3] if price_node else 0
            phone_node = next(
                (
                    n
                    for n in _nodes(xml)
                    if n["cls"] == "EditText"
                    and n.get("bounds")
                    and n["bounds"][1] > min_y
                ),
                None,
            )
            if phone_node:
                _type_into(transport, phone_node["bounds"], phone_digits)
                steps.append("filled phone")
                time.sleep(0.5)

        # 8. Category Selection (Grafické karty)
        cat_btn = next((n for n in _nodes(xml) if "Vyberte kategorii" in (n.get("desc") or "") and n.get("bounds")), None)
        if cat_btn:
            _tap_box(transport, cat_btn["bounds"])
            time.sleep(1.5)
            # Pick Elektro a počítače -> Komponenty -> Grafické karty
            _tap_category_path(transport, ["Elektro a počítače", "Počítače", "Komponenty", "Grafické karty"])
            steps.append("selected category: Grafické karty")
            time.sleep(1.0)

        # Screenshot of form before submission
        ts = datetime.now(UTC).strftime("%H%M%S")
        evidence_file = Path(get_data_dir()) / "evidence" / "sbazar-android" / f"form-{ts}.png"
        evidence_file.parent.mkdir(parents=True, exist_ok=True)
        transport.screenshot(evidence_file)
        evidence = str(evidence_file)

        if not dry_run:
            # Tap "Náhled nabídky"
            xml = _read_ui(transport)
            preview_btn = next((n for n in _nodes(xml) if "Náhled" in (n.get("desc") or "") and n.get("bounds")), None)
            if preview_btn:
                _tap_box(transport, preview_btn["bounds"])
                time.sleep(2.0)
                # Tap "Zveřejnit nabídku"
                pub_xml = _read_ui(transport)
                pub_btn = next((n for n in _nodes(pub_xml) if any(w in (n.get("desc") or n.get("text") or "").lower() for w in ("zveřejnit", "publikovat", "odeslat")) and n.get("bounds")), None)
                if pub_btn:
                    _tap_box(transport, pub_btn["bounds"])
                    time.sleep(4.0)
                    submitted = True
                    steps.append("ad submitted to SBazar")
        else:
            steps.append("dry run completed; offer not submitted")
            _key_back(transport)
            time.sleep(1.0)
            _key_back(transport)

    except BrokerMaintenanceError:
        errors.append("Телефон занят окном обслуживания.")
    except AndroidBridgeError as exc:
        log.warning("sbazar android publish failed: %s", exc)
        errors.append(f"Ошибка Android-моста SBazar: {exc}")
    finally:
        transport.end()

    ok = submitted if not dry_run else (not errors)
    return _make_result(ok, steps, errors, evidence, dry_run, item_code, final_url)


def _tap_category_path(transport: Any, path: list[str]) -> None:
    for target in path:
        time.sleep(1.0)
        xml = _read_ui(transport)
        node = next((n for n in _nodes(xml) if target.lower() in (n.get("desc") or n.get("text") or "").lower() and n.get("bounds")), None)
        if node:
            _tap_box(transport, node["bounds"])
        else:
            transport.scroll_down()
            time.sleep(0.8)
            xml = _read_ui(transport)
            node = next((n for n in _nodes(xml) if target.lower() in (n.get("desc") or n.get("text") or "").lower() and n.get("bounds")), None)
            if node:
                _tap_box(transport, node["bounds"])


def _make_result(
    ok: bool,
    steps: list[str],
    errors: list[str],
    evidence: str,
    dry_run: bool,
    item_code: str,
    final_url: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "steps": steps,
        "errors": errors,
        "evidence_path": evidence,
        "final_url": final_url,
        "dry_run": dry_run,
        "channel": "android",
        "item_code": item_code,
        "timestamp": datetime.now(UTC).isoformat(),
    }
