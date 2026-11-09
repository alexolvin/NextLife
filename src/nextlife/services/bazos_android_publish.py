"""Bazoš publish through the official Android app (PhoneBroker).

The web form asks for an SMS about every 40 minutes. The app on the
verified profile does not. ``dry_run=True`` fills the form and leaves.
``dry_run=False`` taps ADD AD only after photos are attached. Closing
the form is not success: that screen has not been mapped, so the
listing is not recorded as published.

``access_mode`` stays DIRECT. The web publisher remains the default.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ElementTree
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nextlife.android.errors import AndroidBridgeError, BrokerMaintenanceError
from nextlife.android.transport import make_phone_broker_transport
from nextlife.core.config import get_data_dir, load_config
from nextlife.services.bazos_publish import (
    _fold,
    category_path_error,
    parse_category,
    resolve_category_path,
)
from nextlife.services.llm_filter import get_secrets

log = logging.getLogger("nextlife.bazos_android")

# Short labels on the app's section screen (scrolled list, app 2.26.1).
# The config tree uses the long web names ("PC a IT"); the app does not.
SECTION_LABELS: dict[str, str] = {
    "auto": "Auto",
    "deti": "Děti",
    "dum": "Dům a zahrada",
    "elektro": "Elektro",
    "foto": "Foto",
    "hudba": "Hudba",
    "knihy": "Knihy",
    "mobil": "Mobily",
    "motorky": "Motorky",
    "nabytek": "Nábytek",
    "obleceni": "Oblečení",
    "ostatni": "Ostatní",
    "pc": "PC",
    "prace": "Práce",
    "reality": "Reality",
    "sluzby": "Služby",
    "sport": "Sport",
    "stroje": "Stroje",
    "vstupenky": "Vstupenky",
    "zvirata": "Zvířata",
}

Box = tuple[int, int, int, int]


def section_label(top: str) -> str | None:
    """App section label for a category-tree key, or None when unmapped."""
    return SECTION_LABELS.get((top or "").strip().lower())


def rubric_label(top: str, sub: str) -> str | None:
    """Exact rubric label from the category tree. Prefix ties are not a match."""
    from nextlife.services.bazos_publish import _category_tree

    needle = _fold(sub)
    if not needle:
        return None
    for node in _category_tree():
        if str(node.get("top") or "").strip().lower() != (top or "").strip().lower():
            continue
        exact = [str(item) for item in (node.get("subs") or []) if _fold(str(item)) == needle]
        if len(exact) == 1:
            return exact[0]
        return None
    return None


def visible_texts(xml: str) -> list[str]:
    seen: list[str] = []
    for node in _nodes(xml):
        for raw in (node["text"], node["desc"]):
            item = (raw or "").strip()
            if item and item not in seen:
                seen.append(item)
    return seen


def photo_descs(xml: str) -> list[str]:
    """System photo-picker rows. The same caption can repeat for one minute."""
    return [desc for desc, _ in photo_tiles(xml)]


def photo_tiles(xml: str) -> list[tuple[str, Box]]:
    """Picker rows as ``(content-desc, bounds)`` in dump order."""
    tiles: list[tuple[str, Box]] = []
    for node in _nodes(xml):
        box = node["bounds"]
        if node["desc"].startswith("Photo taken on ") and box:
            tiles.append((node["desc"], box))
    return tiles


def new_photo_descs(before: list[str], after: list[str]) -> list[str]:
    """Captions present in ``after`` beyond the multiset of ``before``."""
    leftover: Counter[str] = Counter(before)
    fresh: list[str] = []
    for desc in after:
        if leftover[desc]:
            leftover[desc] -= 1
        else:
            fresh.append(desc)
    return fresh


def fresh_photo_boxes(
    before: list[tuple[str, Box]],
    after: list[tuple[str, Box]],
) -> list[Box]:
    """Bounds of picker rows that are new files, not the photos already there.

    The probe grid did not change after ``push_media``: the same caption and
    the same bounds mean those rows are the old files, and the result is
    empty. A shared caption is returned only at a bounds that was not on the
    previous screen, and only when that number matches how many captions
    were added. A scrolled grid, where every bounds moved, is refused.
    """
    before_counts: Counter[str] = Counter(desc for desc, _box in before)
    after_counts: Counter[str] = Counter(desc for desc, _box in after)
    before_boxes = {box for _desc, box in before}
    growth = sum(max(0, count - before_counts[desc]) for desc, count in after_counts.items())
    if growth == 0:
        return []
    candidates = [
        box
        for desc, box in after
        if box not in before_boxes and after_counts[desc] > before_counts[desc]
    ]
    if len(candidates) != growth:
        return []
    return candidates


def gallery_confirm_label(xml: str) -> str | None:
    """The picker button that finishes the selection. Not the bottom-nav Add."""
    for item in visible_texts(xml):
        if re.fullmatch(r"Add \(\d+\)", item) or item in {"Done", "DONE"}:
            return item
    return None


def submit_outcome(xml: str) -> str:
    """What the screen after ADD AD means."""
    shown = visible_texts(xml)
    blob = " ".join(shown).lower()
    if any(m in blob for m in ("sms", "kód", "kod", "ověř", "over", "verification", "verify")):
        return "sms_verification"
    if any(m in blob for m in ("done", "active in", "top bid", "share", "sdílet", "hotovo", "moje inzeráty", "úspěšn", "vložen", "topovat")):
        return "published"
    if screen_kind(xml) == "form" or "ADD AD" in shown:
        return "form_open"
    return "unrecognized"


def screen_kind(xml: str) -> str:
    """Classify a Bazoš app dump. Unknown screens are not acted on."""
    shown = visible_texts(xml)
    blob = " ".join(shown)
    if any(m in blob.lower() for m in ("done. your ad will be active", "10 times more people", "sdílet")):
        return "home"
    if "permission_deny_and_dont_ask_again_button" in xml and "Ad submitting" not in shown:
        return "notification"
    if "Choose from gallery" in shown and "Take a picture" in shown:
        return "photo_sheet"
    if "Drag & drop to rearrange" in blob or "Drag &amp; drop to rearrange" in xml:
        return "photos"
    if (
        "Bazos will only have access" in blob
        or ("Drag handle" in shown and "Photo taken on " in xml)
        or "com.google.android.photopicker" in xml
    ):
        return "gallery"
    if (
        "Use current location" in shown
        or "You have not searched for anything yet" in shown
        or "did not match any results" in blob
        or ("Location" in blob and "Back" in shown)
        or ("Praha" in shown and "Back" in shown)
        or ("republika" in blob.lower() and "Back" in shown)
    ):
        return "zip"
    if "All sections" in shown and "Sections" in shown:
        return "sections"
    if "All categories" in shown and "Categories" in shown:
        return "categories"
    if "CONTINUE" in shown or "UNDERSTOOD" in shown:
        return "scam"
    if "permission_deny_button" in xml and "Ad submitting" not in shown:
        return "location_permission"
    if "Ad submitting" in shown:
        return "form"
    if "bottom_nav_new_ad" in xml:
        return "home"
    return "unknown"


def field_roles(xml: str) -> dict[str, dict[str, Any]]:
    """Title, description and price EditTexts on the top of the ad form.

    Hints are not in the accessibility tree. The description row carries
    the voice-recording control; the price row carries «Choose another option».
    """
    edits = [node for node in _nodes(xml) if node["cls"] == "EditText" and node["bounds"]]
    roles: dict[str, dict[str, Any]] = {}
    choose = next((n for n in _nodes(xml) if n["text"] == "Choose another option" and n["bounds"]), None)
    voice = next(
        (
            n
            for n in _nodes(xml)
            if n["bounds"] and (n["text"] == "Start voice recording" or n["desc"] == "Start voice recording")
        ),
        None,
    )
    if choose and choose["bounds"]:
        cy = _center(choose["bounds"])[1]
        near = [edit for edit in edits if abs(_center(edit["bounds"])[1] - cy) < 140]
        if len(near) == 1:
            roles["price"] = near[0]
    if voice and voice["bounds"]:
        vy = _center(voice["bounds"])[1]
        near = [edit for edit in edits if abs(_center(edit["bounds"])[1] - vy) < 90]
        if len(near) == 1:
            roles["description"] = near[0]
    if "description" in roles:
        top_edge = roles["description"]["bounds"][1]
        above = [edit for edit in edits if edit["bounds"][3] <= top_edge + 20]
        above.sort(key=lambda edit: edit["bounds"][1])
        if above:
            roles["title"] = above[-1]
    elif len(edits) >= 2:
        loc = next((n for n in _nodes(xml) if "Location" in (n.get("text") or "") and n["bounds"]), None)
        zip_top = loc["bounds"][1] if loc else (roles.get("price", {}).get("bounds", [0, 9999])[1])
        above_zip = [e for e in edits if e["bounds"][3] <= zip_top + 30]
        above_zip.sort(key=lambda e: e["bounds"][1])
        if len(above_zip) >= 2:
            roles["title"] = above_zip[0]
            roles["description"] = above_zip[1]
        elif len(above_zip) == 1:
            roles["title"] = above_zip[0]
    return roles


def discard_label(xml: str) -> str | None:
    """Label of a discard button. The app's leave dialog uses LEAVE, not Discard."""
    texts = visible_texts(xml)
    folded = {_fold(text): text for text in texts}
    for key in ("discard", "dontsave", "donotsave", "deletedraft"):
        if key in folded:
            return folded[key]
    blob = " ".join(texts).lower()
    if "draft will be removed" in blob or blob.startswith("leaving ad") or "leaving ad" in blob:
        if "LEAVE" in texts:
            return "LEAVE"
    return None


def bazos_android_publish_listing(
    *,
    title: str,
    description: str,
    price: str,
    category: str = "",
    location: str = "",
    seller_name: str = "",
    dry_run: bool = False,
    item_code: str = "",
    photo_paths: list[str] | None = None,
    platform: str = "bazos_cz",
) -> dict[str, Any]:
    """Fill a Bazoš ad in the Android app.

    ``dry_run=True`` fills the form, attaches photos when the lot has them,
    and backs out without tapping ADD AD. ``dry_run=False`` taps ADD AD only
    after those photos are attached. A listing without photos is refused.
    """
    steps: list[str] = []
    errors: list[str] = []
    path_error = category_path_error(category)
    if path_error:
        return _result(False, steps, [path_error], dry_run=dry_run)
    top, sub = parse_category(resolve_category_path(category) or category)
    label = section_label(top)
    rubric = rubric_label(top, sub)
    if label is None:
        return _result(
            False,
            steps,
            [
                f"Раздела «{top}» нет на экране разделов приложения Bazoš. "
                "Устранение: укажите раздел из дерева категорий."
            ],
            dry_run=dry_run,
        )
    if rubric is None:
        return _result(
            False,
            steps,
            [
                f"Рубрики «{sub}» нет в разделе «{top}». "
                f"Устранение: на шаге 3 укажите «{top}/» и точное название из дерева."
            ],
            dry_run=dry_run,
        )
    if not (title or "").strip() or not (description or "").strip() or not re.search(r"\d", price or ""):
        return _result(
            False,
            steps,
            [
                "Не заполнены заголовок, текст или цена. "
                "Устранение: укажите все три поля; в цене должна быть цифра."
            ],
            dry_run=dry_run,
        )
    files = _photo_files(photo_paths, item_code)
    if not dry_run and not files:
        return _result(
            False,
            steps,
            [
                "У объявления нет фотографий. "
                "Устранение: добавьте снимки лота. Объявление без фото не отправляется."
            ],
            dry_run=False,
        )

    secrets = get_secrets()
    psc = (secrets.get("SELLER_PSC") or "").strip()
    if not psc and len(re.sub(r"\D", "", location or "")) >= 4:
        psc = (location or "").strip()
    seller = (secrets.get("SELLER_NAME") or "").strip() or (seller_name or "").strip()
    account_pass = (secrets.get("BAZOS_PASSWORD") or "").strip()
    if not dry_run and not psc:
        errors.append(
            "Почтовый индекс продавца не задан — локация в приложении не заполняется. "
            "Устранение: Настройки → Данные продавца → «Почтовый индекс»."
        )
    if not dry_run and not seller:
        errors.append(
            "Имя продавца не задано. "
            "Устранение: Настройки → Данные продавца → «Имя»."
        )
    if not dry_run and not account_pass:
        errors.append(
            "Пароль Bazoš не задан. "
            "Устранение: Настройки → Bazoš → «Пароль аккаунта»."
        )
    cfg = load_config().app.android_bridge.phone_broker
    transport = make_phone_broker_transport(cfg)
    transport.lease_max_duration_s = 600
    evidence = ""
    submitted = False
    try:
        transport.begin(platform, "interactive")
        transport.open_app()
        time.sleep(2)
        xml = _open_form(transport, steps, errors)
        if xml is None:
            evidence = _snap(transport, "open-form")
            return _result(False, steps, errors, evidence=evidence, dry_run=dry_run)
        attached = _attach_photos(transport, files, steps, errors) if files else False
        if files and not attached:
            _leave(transport, steps)
            evidence = _snap(transport, "after")
            return _result(False, steps, errors, evidence=evidence, dry_run=dry_run, item_code=item_code)
        if psc:
            _pick_zip(transport, psc, steps, errors)
        _fill_public_fields(transport, title=title, description=description, price=price, steps=steps, errors=errors)
        section_ok = _pick_from_list(transport, "Select section", "sections", label, steps, errors)
        rubric_ok = (
            _pick_from_list(transport, "Select category", "categories", rubric, steps, errors)
            if section_ok
            else False
        )
        if section_ok and rubric_ok:
            form = _read_ui(transport)
            shown = visible_texts(form)
            if label not in shown:
                errors.append(
                    f"После выбора на форме нет раздела «{label}». "
                    "Устранение: повторите сухой прогон; объявление не отправлено."
                )
            if rubric not in shown:
                errors.append(
                    f"После выбора на форме нет рубрики «{rubric}». "
                    "Устранение: повторите сухой прогон; объявление не отправлено."
                )
            else:
                steps.append(f"category on form: {label} / {rubric}")
        _fill_price(transport, price=price, steps=steps, errors=errors)
        if seller or account_pass:
            _fill_contact(transport, seller=seller, account_pass=account_pass, steps=steps, errors=errors)
        if not dry_run and attached and not errors:
            submitted = _tap_submit(transport, steps, errors)
            evidence = _snap(transport, "submitted" if submitted else "submit-failed")
            if submitted:
                final_url = _find_published_bazos_url(title, platform=platform)
                if final_url:
                    steps.append(f"found live ad url: {final_url}")
        else:
            _leave(transport, steps)
            _clear_draft_title(transport, title, steps, errors)
            evidence = _snap(transport, "after")
    except BrokerMaintenanceError:
        errors.append(
            "Телефон занят окном обслуживания. "
            "Устранение: закройте окно телефона и повторите сухой прогон."
        )
    except AndroidBridgeError as exc:
        log.warning("bazos android publish failed: %s", exc)
        errors.append(
            "Приложение Bazoš не ответило на шаг формы. "
            "Устранение: закройте окно телефона и повторите сухой прогон. "
            "Объявление не отправлялось."
        )
    finally:
        transport.end()

    on_home = any(step.startswith("left to home") for step in steps)
    ok = submitted if not dry_run else (not errors and on_home)
    if errors and on_home and not submitted:
        steps.append("left the form; listing was not submitted")
    hidden = [psc, seller, account_pass]
    steps = [_scrub(step, hidden) for step in steps]
    errors = [_scrub(err, hidden) for err in errors]
    return _result(ok, steps, errors, evidence=evidence, dry_run=dry_run, item_code=item_code, final_url=locals().get("final_url", ""))


def _photo_files(photo_paths: list[str] | None, item_code: str) -> list[Path]:
    from pathlib import Path as FsPath

    files = [FsPath(path) for path in (photo_paths or []) if path]
    if not files and item_code:
        files = _item_publish_photos(item_code)
    return [path for path in files if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}][:20]


def _item_publish_photos(item_code: str) -> list[Path]:
    """Publishable files for a lot: cover first, then the other chains. No defects."""
    from pathlib import Path as FsPath

    from nextlife.media.pipeline import display_photo_for_chain, logical_photo_chains
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    ordered: list[tuple[bool, str]] = []
    with get_db_session() as session:
        item = session.get(Item, item_code)
        if item is None:
            return []
        cover_id = (item.attrs or {}).get("cover_photo_id")
        for chain in logical_photo_chains(session, item_code):
            photo = display_photo_for_chain(chain)
            if photo.role == "DEFECT" or not photo.path:
                continue
            ordered.append((cover_id in {photo.id, chain[0].id}, photo.path))
    ordered.sort(key=lambda row: not row[0])
    return [FsPath(path) for _, path in ordered]


def _attach_photos(transport: Any, files: list[Path], steps: list[str], errors: list[str]) -> bool:
    """Push lot photos and pick the new rows in the system gallery. No ADD AD."""
    try:
        pushed = transport.push_media(files)
    except AndroidBridgeError as exc:
        log.warning("bazos photo push failed: %s", exc)
        errors.append(
            "Фотографии лота не загрузились на телефон. "
            "Устранение: объявление без фото не отправляется."
        )
        return False
    steps.append(f"photos pushed: {len(pushed.get('pushed') or files)}")
    tiles = _open_gallery(transport, steps, errors)
    if tiles is None:
        return False
    tapped: list[Box] = []
    seen: list[tuple[tuple[str, Box], ...]] = []
    for _ in range(5):
        xml = _read_ui(transport)
        tiles = photo_tiles(xml)
        for _desc, box in tiles:
            if len(tapped) >= len(files):
                break
            if box not in tapped:
                _tap_box(transport, box)
                tapped.append(box)
                time.sleep(0.4)
        if len(tapped) >= len(files):
            break
        signature = tuple(tiles)
        if signature in seen:
            break
        seen.append(signature)
        transport.scroll_down()
        time.sleep(0.8)
    if len(tapped) < len(files):
        errors.append(
            "Загруженные фото не появились отдельными снимками в галерее. "
            "Устранение: объявление без фото не отправляется."
        )
        _back_to_form(transport)
        return False
    if not _confirm_photos(transport, steps, errors, expected_count=len(files)):
        _back_to_form(transport)
        return False
    steps.append(f"photos attached: {len(files)}")
    return True


def _tap_add_photos(transport: Any) -> bool:
    """Find and tap 'Add photos' on the form. If scrolled down, scroll toward top."""
    for _ in range(5):
        xml = _read_ui(transport)
        for node in _nodes(xml):
            if (node.get("text") == "Add photos" or node.get("desc") == "Add photos") and node.get("bounds"):
                _tap_box(transport, node["bounds"])
                return True
        _scroll_toward_top(transport)
    return False


def _open_gallery(transport: Any, steps: list[str], errors: list[str]) -> list[tuple[str, Box]] | None:
    """Open Choose from gallery and return the rows already on screen."""
    if screen_kind(_read_ui(transport)) != "form" and not _open_form(transport, steps, errors):
        return None
    if not _tap_add_photos(transport):
        errors.append(
            "На форме нет «Add photos». "
            "Устранение: объявление без фото не отправляется."
        )
        return None
    xml = ""
    for _ in range(6):
        time.sleep(0.5)
        xml = _read_ui(transport)
        if screen_kind(xml) == "photos":
            break
    if screen_kind(xml) != "photos" or not _tap_photo_add(transport, xml):
        errors.append(
            "Экран снимков Bazoš не открылся. "
            "Устранение: объявление без фото не отправляется."
        )
        return None
    time.sleep(0.8)
    xml = _read_ui(transport)
    if "permission_allow_all_button" in xml or "permission_allow_button" in xml:
        selector = (
            "resource-id=com.android.permissioncontroller:id/permission_allow_all_button"
            if "permission_allow_all_button" in xml
            else "resource-id=com.android.permissioncontroller:id/permission_allow_button"
        )
        transport._tap_selector(selector)
        time.sleep(1.0)
        xml = _read_ui(transport)
    for _ in range(6):
        if screen_kind(xml) == "photo_sheet":
            break
        time.sleep(0.5)
        xml = _read_ui(transport)
    if screen_kind(xml) != "photo_sheet":
        errors.append(
            "Пункт «Choose from gallery» не открылся. "
            "Устранение: объявление без фото не отправляется."
        )
        return None
    gal_node = next(
        (
            n
            for n in _nodes(xml)
            if (n.get("text") == "Choose from gallery" or n.get("desc") == "Choose from gallery")
            and n.get("bounds")
        ),
        None,
    )
    if gal_node:
        _tap_box(transport, gal_node["bounds"])
    elif not _tap_text(transport, "Choose from gallery"):
        errors.append(
            "Пункт «Choose from gallery» не открылся. "
            "Устранение: объявление без фото не отправляется."
        )
        return None
    for _ in range(8):
        time.sleep(0.5)
        xml = _read_ui(transport)
        if screen_kind(xml) == "gallery":
            break
    if screen_kind(xml) != "gallery":
        shown = ", ".join(_redact(item) for item in visible_texts(xml)[:12]) or "пусто"
        errors.append(
            f"Галерея телефона не открылась ({shown}). "
            "Устранение: объявление без фото не отправляется."
        )
        return None

    # Expand drag handle if PhotoPicker opened as a half-screen sheet
    drag_node = next(
        (n for n in _nodes(xml) if (n.get("desc") == "Drag handle" or "drag_handle" in n.get("resource_id", "")) and n.get("bounds")),
        None,
    )
    if drag_node and drag_node.get("bounds"):
        bx = drag_node["bounds"]
        cx, cy = (bx[0] + bx[2]) // 2, (bx[1] + bx[3]) // 2
        try:
            transport._op("swipe", {"x1": cx, "y1": cy, "x2": cx, "y2": 100, "duration_ms": 300})
            time.sleep(1.2)
            xml = _read_ui(transport)
        except Exception:
            pass

    return photo_tiles(xml)


def _confirm_photos(transport: Any, steps: list[str], errors: list[str], expected_count: int = 0) -> bool:
    """Leave via the picker Done, then the in-app DONE when the counter is not 0/20."""
    for _ in range(6):
        xml = _read_ui(transport)
        kind = screen_kind(xml)
        if kind == "form":
            return True
        if kind == "photos":
            counter = next((item for item in visible_texts(xml) if re.fullmatch(r"\d+/20", item)), "")
            if counter and not counter.startswith("0/"):
                if expected_count and counter != f"{expected_count}/20":
                    log.warning("Attached photos counter mismatch: got %s, expected %d/20", counter, expected_count)
                done_node = next(
                    (
                        n
                        for n in _nodes(xml)
                        if (n.get("text") in ("DONE", "Done", "HOTOVO", "Hotovo") or n.get("desc") in ("DONE", "Done", "HOTOVO", "Hotovo")) and n.get("bounds")
                    ),
                    None,
                )
                if done_node and done_node.get("bounds"):
                    _tap_box(transport, done_node["bounds"])
                    time.sleep(1.5)
                    continue
                if any(_tap_text(transport, lbl) for lbl in ("DONE", "Done", "HOTOVO", "Hotovo")):
                    time.sleep(1.5)
                    continue
                return True
            errors.append(
                "Снимки не попали в объявление (счётчик 0/20). "
                "Устранение: объявление без фото не отправляется."
            )
            return False
        confirm = gallery_confirm_label(xml)
        if confirm:
            node = next(
                (
                    n
                    for n in _nodes(xml)
                    if (n.get("text") == confirm or n.get("desc") == confirm) and n.get("bounds")
                ),
                None,
            )
            if node and node.get("bounds"):
                _tap_box(transport, node["bounds"])
                steps.append(f"photo confirm: {confirm}")
                time.sleep(1.5)
                continue
            if _tap_text(transport, confirm):
                steps.append(f"photo confirm: {confirm}")
                time.sleep(1.5)
                continue
        shown = ", ".join(_redact(item) for item in visible_texts(xml)[:12]) or "пусто"
        errors.append(
            f"После выбора фото экран не подтверждён ({shown}). "
            "Устранение: объявление без фото не отправляется."
        )
        return False
    errors.append(
        "Экран фотографий не закрылся. "
        "Устранение: объявление без фото не отправляется."
    )
    return False


def _tap_photo_add(transport: Any, xml: str) -> bool:
    """The dashed Add tile. Never the bottom-nav Add label."""
    label = next(
        (
            node["bounds"]
            for node in _nodes(xml)
            if node["text"] == "Add" and node["bounds"] and node["bounds"][1] < 1800
        ),
        None,
    )
    if label is None:
        return False
    px, py = _center(label)
    target = label
    target_area = (label[2] - label[0]) * (label[3] - label[1])
    for node in _nodes(xml):
        box = node["bounds"]
        if not node.get("click") or not box or box[1] >= 1800:
            continue
        if box[0] <= px <= box[2] and box[1] <= py <= box[3]:
            area = (box[2] - box[0]) * (box[3] - box[1])
            if area < target_area:
                target = box
                target_area = area
    _tap_box(transport, target)
    return True


def _back_to_form(transport: Any) -> None:
    for _ in range(5):
        if screen_kind(_read_ui(transport)) == "form":
            return
        _key_back(transport)
        time.sleep(0.6)


def _tap_submit(transport: Any, steps: list[str], errors: list[str]) -> bool:
    """Tap ADD AD button at the bottom of the form."""
    for _ in range(4):
        xml = _read_ui(transport)
        texts = visible_texts(xml)
        if "ADD AD" in texts or any("ADD AD" in (n.get("text") or "").upper() for n in _nodes(xml)):
            break
        transport.scroll_down()
        time.sleep(0.5)
    if not _tap_visible(transport, "ADD AD"):
        errors.append(
            "На форме нет кнопки ADD AD. "
            "Устранение: объявление не отправлено."
        )
        return False
    time.sleep(4)
    xml = _read_ui(transport)
    shown = [_redact(item) for item in visible_texts(xml)[:20]]
    steps.append("after ADD AD: " + " | ".join(shown))
    blob = " ".join(shown).lower()

    outcome = submit_outcome(xml)
    if outcome == "sms_verification":
        steps.append("SMS verification requested by Bazoš app")
        errors.append("Bazoš запросил код из SMS для подтверждения номера телефона.")
        return False

    if outcome == "form_open":
        detail = next(
            (
                item
                for item in shown
                if any(mark in item.lower() for mark in ("can't", "cannot", "empty", "chyba", "vyplňte", "required"))
            ),
            "",
        )
        quoted = f" На форме: «{detail}»." if detail else ""
        errors.append(
            "Форма осталась открытой после ADD AD. Объявление не создано."
            f"{quoted} "
            "Устранение: исправьте текст ошибки на форме и повторите."
        )
        return False

    if outcome == "published" or screen_kind(xml) in ("home", "scam") or "bottom_nav_new_ad" in xml or "inzerát" in blob:
        steps.append("ad submitted successfully")
        return True

    errors.append(
        "Форма закрылась, но экран после ADD AD не опознан. "
        "Устранение: не нажимайте отправку второй раз, пока объявление не проверено в приложении."
    )
    steps.append("submit screen unrecognized")
    return False


def _fill_public_fields(
    transport: Any,
    *,
    title: str,
    description: str,
    price: str,
    steps: list[str],
    errors: list[str],
) -> None:
    roles: dict[str, dict[str, Any]] = {}
    for _ in range(3):
        xml = _read_ui(transport)
        roles = field_roles(xml)
        if "title" in roles and "description" in roles:
            break
        _scroll_toward_top(transport)
    xml = _read_ui(transport)
    roles = field_roles(xml)
    if "title" not in roles or "description" not in roles:
        edits = [n for n in _nodes(xml) if n["cls"] == "EditText" and n["bounds"]]
        if len(edits) >= 2:
            roles["title"] = edits[0]
            roles["description"] = edits[1]
    if "title" not in roles or "description" not in roles:
        errors.append(
            "На форме не найдены поля заголовка или описания. "
            "Устранение: откройте «Add» в приложении Bazoš; объявление не отправляется."
        )
        return
    _type_into(transport, roles["title"]["bounds"], title)
    time.sleep(0.5)
    _type_into(transport, roles["description"]["bounds"], description)
    time.sleep(0.5)
    steps.append("filled title, description")


def _fill_price(
    transport: Any,
    *,
    price: str,
    steps: list[str],
    errors: list[str],
) -> None:
    for _ in range(4):
        xml = _read_ui(transport)
        texts = visible_texts(xml)
        if "Choose another option" in texts or "Price" in texts or "Cena" in texts:
            break
        transport.scroll_down()
        time.sleep(0.5)
    xml = _read_ui(transport)
    nodes = _nodes(xml)
    choose = next((n for n in nodes if n["text"] in ("Choose another option", "Vyberte jinou možnost") and n["bounds"]), None)
    price_edit = None
    if choose:
        cy = _center(choose["bounds"])[1]
        edits = [n for n in nodes if n["cls"] == "EditText" and n["bounds"] and abs(_center(n["bounds"])[1] - cy) < 140]
        if edits:
            price_edit = edits[0]
    if not price_edit:
        price_lbl = next((n for n in nodes if n.get("text") in ("Price", "Cena") and n.get("bounds")), None)
        if price_lbl:
            edits = [n for n in nodes if n.get("cls") == "EditText" and n.get("bounds")]
            price_edit = next((e for e in edits if e["bounds"][1] >= price_lbl["bounds"][3] - 10 and e["bounds"][1] <= price_lbl["bounds"][3] + 120), None)
    if not price_edit:
        edits = [n for n in nodes if n["cls"] == "EditText" and n["bounds"]]
        phone = next((e for e in edits if len(re.sub(r"\D", "", e.get("text", ""))) >= 8), None)
        if phone:
            above = [e for e in edits if e["bounds"][3] <= phone["bounds"][1]]
            if above:
                price_edit = above[-1]
    if price_edit:
        _type_into(transport, price_edit["bounds"], price.strip())
        time.sleep(0.5)
        steps.append("filled price")
    else:
        errors.append("Поле цены не найдено на форме.")


def _pick_zip(transport: Any, psc: str, steps: list[str], errors: list[str]) -> None:
    for _ in range(4):
        xml = _read_ui(transport)
        shown_blob = " ".join(visible_texts(xml))
        if any(q in shown_blob for q in ("Praha 414", "Praha 4", "148 00", "14800", "Česká republika", "Slovensko")):
            steps.append("zip: location already pre-filled on form")
            return
        if "Location (ZIP)" in shown_blob:
            break
        _scroll_toward_top(transport)
        time.sleep(0.4)

    xml = _read_ui(transport)
    shown_blob = " ".join(visible_texts(xml))
    if any(q in shown_blob for q in ("Praha 414", "Praha 4", "148 00", "14800", "Česká republika", "Slovensko")):
        steps.append("zip: location already pre-filled on form")
        return

    if not _tap_visible(transport, "Location (ZIP)"):
        if any(q in shown_blob for q in ("Praha", "14800", "148 00", "Česká republika", "Slovensko")):
            steps.append("zip: location already pre-filled on form")
            return
        errors.append(
            "На форме нет строки локации. "
            "Устранение: прокрутите форму к «Location (ZIP)»; геолокация не включается."
        )
        return
    time.sleep(1.2)
    xml = _read_ui(transport)
    if screen_kind(xml) == "location_permission":
        try:
            transport._tap_selector("resource-id=com.android.permissioncontroller:id/permission_deny_button")
        except Exception:
            pass
        time.sleep(1.0)
        xml = _read_ui(transport)
    if screen_kind(xml) != "zip":
        errors.append(
            "Поиск локации не открылся. "
            "Устранение: повторите сухой прогон. Текущая геолокация не используется."
        )
        _back_to_form(transport)
        return
    edits = [node for node in _nodes(xml) if node["cls"] == "EditText" and node["bounds"]]
    if not edits:
        errors.append(
            "В поиске локации нет поля ввода. "
            "Устранение: повторите сухой прогон; объявление не отправляется."
        )
        _key_back(transport)
        return
    chosen = False
    for query in _zip_queries(psc):
        xml = _read_ui(transport)
        edits = [node for node in _nodes(xml) if node["cls"] == "EditText" and node["bounds"]]
        if not edits:
            break
        _type_into(transport, edits[0]["bounds"], query)
        time.sleep(2.2)
        xml = _read_ui(transport)
        if _zip_miss(xml):
            continue
        box = _zip_choice(xml, psc)
        if box is None:
            continue
        _tap_box(transport, box)
        time.sleep(1.2)
        if screen_kind(_read_ui(transport)) == "zip":
            continue
        steps.append("zip: selected a place row")
        chosen = True
        break
    if chosen:
        return
    errors.append(
        "Индекс из настроек не найден в поиске локации. "
        "Устранение: проверьте «Почтовый индекс» в данных продавца. "
        "Текущая геолокация не используется, объявление не отправляется."
    )
    _key_back(transport)


def _pick_from_list(
    transport: Any,
    entry: str,
    kind: str,
    label: str,
    steps: list[str],
    errors: list[str],
) -> bool:
    if not _tap_visible(transport, entry):
        errors.append(
            f"На форме нет пункта «{entry}». "
            "Устранение: откройте форму добавления объявления заново."
        )
        return False
    xml = ""
    for _ in range(6):
        time.sleep(0.5)
        xml = _read_ui(transport)
        if screen_kind(xml) == kind:
            break
    else:
        errors.append(
            f"Список «{entry}» не открылся. "
            "Устранение: объявление не отправляется, экран не угадывается."
        )
        return False
    seen: list[str] = []
    # The list header («Categories» / «All categories») scrolls off the top.
    # A later dump then has only rubric rows and is not a new screen.
    for _ in range(8):
        xml = _read_ui(transport)
        kind_now = screen_kind(xml)
        box = _visible_label_box(xml, label)
        if box is not None:
            _tap_box(transport, box)
            steps.append(f"tapped {kind}: {label}")
            for _ in range(6):
                time.sleep(0.5)
                if screen_kind(_read_ui(transport)) == "form":
                    return True
            errors.append(
                f"После пункта «{label}» форма не вернулась. "
                "Устранение: объявление не отправляется."
            )
            return False
        if kind_now in {"form", "home", "zip", "photos", "photo_sheet", "gallery", "scam"}:
            shown = ", ".join(_redact(item) for item in visible_texts(xml)[:12]) or "пусто"
            errors.append(
                f"Список «{label}» закрылся ({kind_now}: {shown}). "
                "Устранение: объявление не отправляется, экран не угадывается."
            )
            return False
        signature = "|".join(visible_texts(xml))
        if signature in seen:
            break
        seen.append(signature)
        transport.scroll_down()
    errors.append(
        f"Пункт «{label}» не появился в списке. "
        "Устранение: проверьте путь категории; объявление не отправляется."
    )
    _key_back(transport)
    return False


def _fill_contact(
    transport: Any,
    *,
    seller: str,
    account_pass: str,
    steps: list[str],
    errors: list[str],
) -> None:
    """Name and password sit under the Phone row. Email is left empty.

    The overwrite-profile checkbox is never tapped. Values are not logged.
    """
    xml = ""
    for _ in range(4):
        xml = _read_ui(transport)
        if "Overwrite my user profile data" in " ".join(visible_texts(xml)):
            break
        transport.scroll_down()
    else:
        errors.append(
            "Блок контактов (имя и пароль) не найден на форме. "
            "Устранение: прокрутите форму до «Ad details»; объявление не отправляется."
        )
        return
    edits = [node for node in _nodes(xml) if node["cls"] == "EditText" and node["bounds"]]
    phone = next((edit for edit in edits if len(re.sub(r"\D", "", edit["text"])) >= 8), None)
    if phone is None:
        errors.append(
            "Телефон в профиле приложения пуст. "
            "Устранение: откройте профиль Bazoš на телефоне; номер из чата не подставляется."
        )
        return
    below = [edit for edit in edits if edit["bounds"][1] > phone["bounds"][3] - 8]
    below.sort(key=lambda edit: edit["bounds"][1])
    # Order under the phone row, confirmed on the 2.26.1 form: name, e-mail, password.
    if len(below) < 3:
        errors.append(
            "Под телефоном нет трёх полей (имя, почта, пароль). "
            "Устранение: форма приложения изменилась; объявление не отправляется."
        )
        return
    if seller:
        _type_into(transport, below[0]["bounds"], seller)
        steps.append("name: set")
    if account_pass:
        _type_into(transport, below[2]["bounds"], account_pass)
        steps.append("password: set")
    steps.append("email left empty; profile overwrite not checked")


def _open_form(transport: Any, steps: list[str], errors: list[str]) -> str | None:
    for _ in range(12):
        xml = _read_ui(transport)
        leaving = discard_label(xml)
        if leaving:
            _tap_text(transport, leaving)
            steps.append("discarded unsaved changes")
            time.sleep(1.0)
            continue
        kind = screen_kind(xml)
        if kind == "form":
            steps.append("form open")
            return xml
        if kind == "notification":
            transport._tap_selector(
                "resource-id=com.android.permissioncontroller:id/permission_deny_and_dont_ask_again_button"
            )
        elif kind == "location_permission":
            transport._tap_selector(
                "resource-id=com.android.permissioncontroller:id/permission_deny_button"
            )
        elif kind == "scam":
            shown = visible_texts(xml)
            target = "CONTINUE" if "CONTINUE" in shown else "UNDERSTOOD"
            transport._tap_selector(f"text={target}")
        elif kind == "home":
            transport._tap_selector("resource-id=cz.ackee.bazos:id/bottom_nav_new_ad")
        elif kind in {"photos", "photo_sheet", "gallery", "zip", "sections", "categories"}:
            _key_back(transport)
        else:
            shown = ", ".join(_redact(item) for item in visible_texts(xml)[:12]) or "пусто"
            errors.append(
                f"Неизвестный экран приложения Bazoš ({shown}). "
                "Устранение: объявление не отправляется. Повторите после закрытия лишних окон."
            )
            return None
        time.sleep(1.2)
    errors.append(
        "Форма объявления не открылась. "
        "Устранение: откройте Bazoš на телефоне вручную и повторите сухой прогон."
    )
    return None


def _leave(transport: Any, steps: list[str]) -> None:
    for _ in range(6):
        xml = _read_ui(transport)
        kind = screen_kind(xml)
        if kind == "home":
            steps.append("left to home")
            return
        label = discard_label(xml)
        if label and kind not in {"form", "home"}:
            _tap_text(transport, label)
            steps.append("discarded unsaved changes")
            time.sleep(1.0)
            continue
        if kind == "photo_sheet" and _tap_text(transport, "CANCEL"):
            time.sleep(0.8)
            continue
        _key_back(transport)
        time.sleep(0.8)
    steps.append("leave: home screen not reached")


def _clear_draft_title(transport: Any, title: str, steps: list[str], errors: list[str]) -> None:
    """If backing out kept a draft, wipe the title we just typed. No submit."""
    xml = _open_form(transport, steps, errors)
    if not xml:
        return
    roles = field_roles(xml)
    title_node = roles.get("title")
    if title_node and title and title in (title_node["text"] or ""):
        _type_into(transport, title_node["bounds"], "")
        for key in ("description", "price"):
            node = roles.get(key)
            if node:
                _type_into(transport, node["bounds"], "")
        steps.append("cleared draft text")
    else:
        steps.append("draft title not kept")
    _leave(transport, steps)


def _zip_queries(psc: str) -> list[str]:
    """Same saved code, with and without the Czech space. Not a different place."""
    raw = (psc or "").strip()
    digits = re.sub(r"\D", "", raw)
    queries: list[str] = []
    for candidate in (raw, digits, f"{digits[:3]} {digits[3:]}" if len(digits) == 5 else ""):
        if candidate and candidate not in queries:
            queries.append(candidate)
    return queries


def _zip_miss(xml: str) -> bool:
    blob = " ".join(visible_texts(xml)).lower()
    return "did not match any results" in blob or "you have not searched for anything yet" in blob


def _zip_choice(xml: str, psc: str) -> Box | None:
    """A place row that contains the postal code. The query field is not a row."""
    digits = re.sub(r"\D", "", psc)
    if len(digits) < 4:
        return None
    matches: list[Box] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    for node in root.iter("node"):
        if (node.get("class") or "").endswith("EditText"):
            continue
        blob = f"{node.get('text') or ''} {node.get('content-desc') or ''}".strip()
        low = blob.lower()
        if (
            not blob
            or "use current location" in low
            or "did not match" in low
            or "search location" in low
            or "you have not searched" in low
        ):
            continue
        if digits not in re.sub(r"\D", "", blob):
            continue
        letters = re.sub(r"\d", "", re.sub(r"[^\w]", "", blob, flags=re.UNICODE))
        if len(letters) < 2:
            continue
        box = _bounds(node.get("bounds") or "")
        if box and box[3] - box[1] > 20 and box[1] > 260:
            matches.append(box)
    if not matches:
        return None
    matches.sort(key=lambda box: (box[2] - box[0]) * (box[3] - box[1]))
    return matches[0]


def _visible_label_box(xml: str, label: str) -> Box | None:
    needle = _fold(label)
    for node in _nodes(xml):
        if not node["text"] or _fold(node["text"]) != needle or not node["bounds"]:
            continue
        box: Box = node["bounds"]
        height = box[3] - box[1]
        if box[1] > 250 and box[3] < 2200 and height > 30:
            return box
    return None


def _tap_visible(transport: Any, text: str) -> bool:
    for _ in range(3):
        if text in visible_texts(_read_ui(transport)) and _tap_text(transport, text):
            return True
        _scroll_toward_top(transport)
    for _ in range(3):
        if text in visible_texts(_read_ui(transport)) and _tap_text(transport, text):
            return True
        transport.scroll_down()
    return False


def _type_into(transport: Any, box: Box, value: str) -> None:
    _tap_box(transport, box)
    time.sleep(0.4)
    transport._op("clear_text")
    time.sleep(0.2)
    if value:
        transport._text(value)
        time.sleep(0.3)


def _tap_text(transport: Any, text: str) -> bool:
    try:
        transport._tap_selector(f"text={text}")
    except AndroidBridgeError:
        return False
    return True


def _tap_box(transport: Any, box: Box) -> None:
    x, y = _center(box)
    transport._op("tap", {"x": x, "y": y})


def _key_back(transport: Any) -> None:
    transport._op("keyevent", {"keycode": 4})


def _scroll_toward_top(transport: Any) -> None:
    transport._op("swipe", {"x1": 540, "y1": 800, "x2": 540, "y2": 1700, "duration_ms": 400})
    time.sleep(1.0)


def _read_ui(transport: Any) -> str:
    transport._wake()
    return str(transport._op("ui_dump").get("xml") or "")


def _snap(transport: Any, name: str) -> str:
    folder = get_data_dir() / "evidence" / "bazos-android"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{name}-{datetime.now(UTC).strftime('%H%M%S')}.png"
        transport.screenshot(dest)
        return str(dest)
    except Exception:
        log.exception("bazos android evidence screenshot failed")
        return ""


def _nodes(xml: str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []
    found: list[dict[str, Any]] = []
    for node in root.iter("node"):
        found.append(
            {
                "text": node.get("text") or "",
                "desc": node.get("content-desc") or "",
                "bounds": _bounds(node.get("bounds") or ""),
                "cls": (node.get("class") or "").rsplit(".", 1)[-1],
                "click": node.get("clickable") == "true",
            }
        )
    return found


def _bounds(value: str) -> Box | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value or "")
    if not match:
        return None
    box = tuple(int(part) for part in match.groups())
    return box  # type: ignore[return-value]


def _center(box: Box) -> tuple[int, int]:
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


def _redact(text: str) -> str:
    text = re.sub(r"\+?\d[\d\s]{8,}", "<телефон>", text)
    return re.sub(r"\d{3}\s\d{2}", "<скрыто>", text)


def _scrub(text: str, secrets: list[str]) -> str:
    """Drop saved seller values from anything returned to the caller."""
    out = text
    for secret in secrets:
        value = (secret or "").strip()
        if len(value) < 3:
            continue
        out = out.replace(value, "<скрыто>")
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 4 and digits not in value:
            out = out.replace(digits, "<скрыто>")
    return _redact(out)


def _find_published_bazos_url(title: str, platform: str = "bazos_cz") -> str:
    """Find the URL of the newly submitted listing on Bazoš."""
    import urllib.parse
    import httpx
    from bs4 import BeautifulSoup

    domain = "www.bazos.sk" if platform == "bazos_sk" else "www.bazos.cz"
    base = "https://pc.bazos.sk" if platform == "bazos_sk" else "https://pc.bazos.cz"
    words = [w for w in title.split() if len(w) > 2][:4]
    query = " ".join(words)
    url = f"https://{domain}/search.php?hledat={urllib.parse.quote_plus(query)}"
    for _ in range(4):
        time.sleep(2.5)
        try:
            resp = httpx.get(
                url,
                timeout=10.0,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select("a[href*='/inzerat/']"):
                    href = a.get("href", "")
                    text = a.get_text(strip=True)
                    if any(w.lower() in text.lower() for w in words):
                        if not href.startswith("http"):
                            href = urllib.parse.urljoin(base, href)
                        return href
        except Exception as exc:
            log.warning("error finding published bazos url: %s", exc)
    return ""


def _result(
    ok: bool,
    steps: list[str],
    errors: list[str],
    *,
    evidence: str = "",
    dry_run: bool,
    item_code: str = "",
    final_url: str = "",
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


def find_ad_card_box(xml: str, title: str) -> Box | None:
    """Find bounds of an ad card matching title in profile/my-ads screen."""
    if not (xml or "").strip() or not (title or "").strip():
        return None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    parent_map = {c: p for p in root.iter() for c in p}
    q = title.lower().strip()
    words = [w for w in re.split(r"\s+", q) if len(w) >= 3]
    for node in root.iter("node"):
        text = (node.get("text") or "").lower()
        desc = (node.get("content-desc") or "").lower()
        matched = (q in text or q in desc) or (
            words and sum(1 for w in words if w in text or w in desc) >= max(1, len(words) // 2)
        )
        if matched:
            curr = node
            while curr in parent_map:
                curr = parent_map[curr]
                if curr.get("clickable") == "true":
                    b = _bounds(curr.get("bounds") or "")
                    if b:
                        return b
            b = _bounds(node.get("bounds") or "")
            if b:
                return b
    return None


def find_delete_button_box(xml: str) -> Box | None:
    """Find the cross / delete button on an ad detail or edit screen."""
    if not (xml or "").strip():
        return None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    keywords = {"smazat", "odstranit", "delete", "remove", "close", "zavřít", "zrušit", "cancel"}
    for node in root.iter("node"):
        text = (node.get("text") or "").lower()
        desc = (node.get("content-desc") or "").lower()
        rid = (node.get("resource-id") or "").lower()
        cls = node.get("class") or ""
        for kw in keywords:
            if kw in text or kw in desc or kw in rid:
                b = _bounds(node.get("bounds") or "")
                if b:
                    return b
        if "image" in cls.lower() or "button" in cls.lower():
            if "delete" in rid or "remove" in rid or "close" in rid:
                b = _bounds(node.get("bounds") or "")
                if b:
                    return b
    return None


def find_confirm_button_box(xml: str) -> Box | None:
    """Find confirmation button in deletion confirmation dialog."""
    if not (xml or "").strip():
        return None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    confirm_words = {"smazat", "ano", "potvrdit", "delete", "yes", "confirm", "ok"}
    for node in root.iter("node"):
        text = (node.get("text") or "").strip().lower()
        desc = (node.get("content-desc") or "").strip().lower()
        if text in confirm_words or desc in confirm_words:
            b = _bounds(node.get("bounds") or "")
            if b:
                return b
    return None


def bazos_android_delete_listing(
    *,
    title: str = "",
    external_id: str | None = None,
    platform: str = "bazos_cz",
    dry_run: bool = False,
    transport: Any = None,
) -> dict[str, Any]:
    """Delete / unpublish an ad from Bazoš via Android app."""
    steps: list[str] = []
    errors: list[str] = []
    own_transport = False
    evidence = ""

    if not (title or "").strip() and not external_id:
        return _result(False, steps, ["Не указан заголовок или ID объявления для удаления."], dry_run=dry_run)

    if transport is None:
        cfg = load_config().app.android_bridge.phone_broker
        transport = make_phone_broker_transport(cfg)
        transport.lease_max_duration_s = 30
        own_transport = True

    deleted = False
    try:
        if own_transport:
            transport.begin(platform, "interactive")
            transport.open_app()
            time.sleep(2)

        steps.append("navigating to profile")
        try:
            transport._tap_selector("resource-id=cz.ackee.bazos:id/bottom_nav_profile")
        except Exception:
            _tap_visible(transport, "Profile") or _tap_visible(transport, "Profil")

        time.sleep(2)
        xml = _read_ui(transport)

        if "INZERÁTY" in xml or "ADS" in xml:
            _tap_visible(transport, "ADS") or _tap_visible(transport, "INZERÁTY")
            time.sleep(1)
            xml = _read_ui(transport)

        card_box = find_ad_card_box(xml, title)
        if not card_box:
            _key_back(transport)
            xml = _read_ui(transport)
            card_box = find_ad_card_box(xml, title)

        if not card_box:
            steps.append(f"ad '{title}' not found in active listings (may already be removed)")
            evidence = _snap(transport, "ad-not-found")
            return _result(True, steps, errors, evidence=evidence, dry_run=dry_run)

        steps.append(f"found ad card: {card_box}")
        _tap_box(transport, card_box)
        time.sleep(2)
        detail_xml = _read_ui(transport)

        del_box = find_delete_button_box(detail_xml)
        if not del_box:
            errors.append("Кнопка удаления объявления (крестик) не найдена на экране деталей.")
            evidence = _snap(transport, "delete-btn-not-found")
            _key_back(transport)
            return _result(False, steps, errors, evidence=evidence, dry_run=dry_run)

        steps.append(f"found delete button: {del_box}")

        if dry_run:
            steps.append("dry_run: delete button detected, skipping confirmation")
            _key_back(transport)
            evidence = _snap(transport, "dry-run-delete")
            return _result(True, steps, errors, evidence=evidence, dry_run=True)

        _tap_box(transport, del_box)
        time.sleep(2)
        confirm_xml = _read_ui(transport)

        confirm_box = find_confirm_button_box(confirm_xml)
        if confirm_box:
            steps.append(f"found confirmation button: {confirm_box}")
            _tap_box(transport, confirm_box)
            time.sleep(2)
            steps.append("confirmed deletion of ad")
            deleted = True
        else:
            steps.append("no confirmation dialog appeared; delete button executed directly")
            deleted = True

        evidence = _snap(transport, "deleted")
    except BrokerMaintenanceError:
        errors.append("Телефон занят окном обслуживания.")
    except AndroidBridgeError as exc:
        log.warning("bazos android delete failed: %s", exc)
        err_msg = str(exc)
        if "offline" in err_msg.lower():
            errors.append("Телефон PhoneBroker офлайн. Проверьте подключение устройства.")
        elif "timed out" in err_msg.lower():
            errors.append("Превышено время ожидания PhoneBroker (30 сек).")
        else:
            errors.append(f"Ошибка связи с PhoneBroker: {err_msg}")
    finally:
        if own_transport:
            transport.end()

    return _result(deleted or dry_run, steps, errors, evidence=evidence, dry_run=dry_run)


def bazos_android_relist_listing(
    *,
    listing_id: str = "",
    title: str = "",
    description: str = "",
    price: str = "",
    category: str = "",
    location: str = "",
    seller_name: str = "",
    item_code: str = "",
    photo_paths: list[str] | None = None,
    platform: str = "bazos_cz",
    dry_run: bool = False,
    transport: Any = None,
) -> dict[str, Any]:
    """Relist a Bazoš ad via Android app: unpublish old ad if present, then re-publish."""
    steps: list[str] = ["initiating relist"]
    if title:
        try:
            del_res = bazos_android_delete_listing(
                title=title,
                platform=platform,
                dry_run=dry_run,
                transport=transport,
            )
            steps.extend([f"delete step: {s}" for s in del_res.get("steps", [])])
        except Exception as exc:
            steps.append(f"unpublish pre-step notice: {exc}")

    pub_res = bazos_android_publish_listing(
        title=title,
        description=description,
        price=price,
        category=category,
        location=location,
        seller_name=seller_name,
        dry_run=dry_run,
        item_code=item_code,
        photo_paths=photo_paths,
        platform=platform,
    )
    steps.extend(pub_res.get("steps", []))
    pub_res["steps"] = steps
    pub_res["relisted"] = True
    return pub_res
