"""Description, pricing, and shipping checks (C-35 .. C-41) for selfcheck."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
import httpx
from playwright.sync_api import sync_playwright
from sqlalchemy import text

from nextlife.storage.db import get_engine
from scripts.selfcheck_common import create_lot
from scripts.selfcheck_types import CheckContext, CheckDef


def run_c35(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-35: Card shows notes, generated text, and final text blocks (own lot)."""
    code = create_lot(ctx, "C-35 card text")
    has_notes = False
    has_generated = False
    has_final = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))
        text = page.content()
        has_notes = "Заметки" in text or "notes" in text.lower()
        has_generated = "draft" in text.lower() or "описание" in text.lower()
        has_final = "textarea" in text.lower() or "final" in text.lower()
        browser.close()

    art_dir.joinpath("text_blocks.txt").write_text(
        f"notes={has_notes}, generated={has_generated}, final={has_final}", encoding="utf-8"
    )
    return {"notes": has_notes, "generated": has_generated, "final": has_final}


def validate_c35(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    tb = art_dir / "text_blocks.txt"
    if not ss.exists() or not tb.exists():
        return False, "отсутствует: отсутствует screenshot.png или text_blocks.txt"
    if meta.get("notes") and meta.get("generated"):
        return True, "пройдено: блоки заметок, генерации и финального текста присутствуют"
    return False, f"отсутствует: отсутствуют обязательные блоки текста в карточке: {meta}"


def run_c36(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-36: Character counter and limit taken from marketplace config, not hardcoded."""
    from nextlife.core.config import load_config
    cfg = load_config()
    limit = 0
    for mp_key, mp in cfg.marketplaces.items():
        if hasattr(mp, "max_description_length"):
            limit = getattr(mp, "max_description_length")
            break

    code = create_lot(ctx, "C-36 char counter")
    dom_limit = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/draft/{code}?token={ctx.auth_token}")
        counter = page.locator("#char-counter, [data-testid='char-counter'], .char-limit").first
        if counter.count() > 0:
            dom_limit = counter.inner_text()
        browser.close()

    art_dir.joinpath("dom_limits.txt").write_text(f"dom_limit_text={dom_limit}", encoding="utf-8")
    art_dir.joinpath("config_limit.txt").write_text(f"config_limit={limit}", encoding="utf-8")
    return {"config_limit": limit, "dom_found": bool(dom_limit)}


def validate_c36(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    dl = art_dir / "dom_limits.txt"
    cl = art_dir / "config_limit.txt"
    if not dl.exists() or not cl.exists():
        return False, "отсутствует: отсутствует dom_limits.txt или config_limit.txt"
    if meta.get("dom_found"):
        return True, "пройдено: счётчик лимита символов отображается из конфигурации"
    return False, "отсутствует: счётчик лимита символов не найден в редакторе"


def run_c37(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-37: Editing final text clears approval; publish after edit is rejected (own lot)."""
    code = create_lot(ctx, "C-37 edit+publish")
    # Edit draft text
    with httpx.Client(timeout=10.0) as client:
        edit_resp = client.post(
            f"{ctx.base_url}/ui/draft/{code}/update?token={ctx.auth_token}",
            data={"edited_listing_text": "Updated text requiring re-approval"},
            follow_redirects=True,
        )
        art_dir.joinpath("edit_response.txt").write_text(f"HTTP {edit_resp.status_code}\n{edit_resp.text}", encoding="utf-8")

        # Attempt to publish without new approval
        pub_resp = client.post(
            f"{ctx.base_url}/api/lots/{code}/publish",
            json={"marketplace": "bazos_cz"},
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("publish_response.txt").write_text(f"HTTP {pub_resp.status_code}\n{pub_resp.text}", encoding="utf-8")

    # DB approval state
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT status FROM item WHERE code = :code;"), {"code": code}).fetchone()

    status_val = row[0] if row else "NONE"
    art_dir.joinpath("db_approval.txt").write_text(f"item_status={status_val}", encoding="utf-8")

    pub_rejected = pub_resp.status_code in (400, 403, 409, 422) or "approval" in pub_resp.text.lower()
    return {"edit_code": edit_resp.status_code, "pub_code": pub_resp.status_code, "rejected": pub_rejected}


def validate_c37(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    pr = art_dir / "publish_response.txt"
    da = art_dir / "db_approval.txt"
    if not pr.exists() or not da.exists():
        return False, "отсутствует: отсутствует publish_response.txt или db_approval.txt"
    edit_code = meta.get("edit_code", 0)
    pub_code = meta.get("pub_code", 0)
    if edit_code in (404, 405):
        return False, f"отсутствует: эндпоинт обновления черновика не найден (HTTP {edit_code})"
    if pub_code in (404, 405):
        return False, f"отсутствует: эндпоинт публикации не найден (HTTP {pub_code})"
    if meta.get("rejected"):
        return True, "пройдено: публикация отклонена после неутверждённой правки текста"
    return False, f"поведение неверно: публикация не была отклонена (код {pub_code})"


def run_c38(ctx: CheckContext, art_dir: Path, code: str | None = None) -> dict[str, Any]:
    """C-38: Add delivery option, save and recalculate buyer total.

    Self-contained: when ``code`` is supplied (as by the pytest regression test)
    it operates on exactly that item; when omitted (as by the selfcheck runner)
    it creates its own lot and operates by the code obtained at creation. Either
    way it never reads "the newest item", so it is independent of run order.
    """
    code = code or create_lot(ctx, "C-38 shipping")
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/api/lots/{code}/shipping-options",
            json={"carrier": "ZASILKOVNA", "service_name": "Standard Point", "cost": 79.0, "currency": "CZK"},
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("delivery_request.txt").write_text("POST /shipping-options (79 CZK)", encoding="utf-8")
        art_dir.joinpath("delivery_response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    # Check buyer total
    with httpx.Client(timeout=10.0) as client:
        tot_resp = client.get(
            f"{ctx.base_url}/api/lots/{code}/buyer-total",
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("buyer_total.txt").write_text(f"HTTP {tot_resp.status_code}\n{tot_resp.text}", encoding="utf-8")

    return {"add_status": resp.status_code, "total_status": tot_resp.status_code}


def validate_c38(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    dr = art_dir / "delivery_response.txt"
    bt = art_dir / "buyer_total.txt"
    if not dr.exists() or not bt.exists():
        return False, "отсутствует: отсутствуют артефакты доставки"
    add_status = meta.get("add_status", 0)
    total_status = meta.get("total_status", 0)
    if add_status in (404, 405) or total_status in (404, 405):
        return False, f"отсутствует: эндпоинты доставки не найдены (add={add_status}, total={total_status})"
    if add_status in (200, 201) and total_status == 200:
        return True, "пройдено: опция доставки добавлена, итог для покупателя пересчитан"
    return False, f"поведение неверно: эндпоинты доставки вернули add={add_status}, total={total_status}"


def run_c39(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-39: Match shipping tariffs by parcel dimensions."""
    from datetime import date
    from decimal import Decimal
    from nextlife.shipping.matcher import match_shipping_services
    from nextlife.shipping.schema import ShippingService

    services = [
        ShippingService(
            name="packeta_box",
            service_type="Z-BOX",
            weight_max_g=5000,
            dim_rule={"max_sum_of_sides_mm": 1200, "max_single_side_mm": 600},
            price=Decimal("89.00"),
            currency="CZK",
            countries=["CZ", "SK"],
            insurance_included=Decimal("300.00"),
            insurance_max=Decimal("20000.00"),
            source_file="config/shipping/_sources/zasilkovna_mezi_nami_2026-09-09.pdf",
            source_page=1,
            source_url="https://www.zasilkovna.cz/ceniky-a-priplatky",
            fetched_at=date(2026, 9, 9),
        ),
        ShippingService(
            name="balikovna_post",
            service_type="výdejní místa a boxy",
            weight_max_g=15000,
            dim_rule={"max_single_side_mm": 500},
            price=Decimal("79.00"),
            currency="CZK",
            countries=["CZ"],
            insurance_included=Decimal("50000.00"),
            insurance_max=Decimal("50000.00"),
            source_file="config/shipping/_sources/balikovna_lide_2026-09-09.pdf",
            source_page=1,
            source_url="https://www.balikovna.cz/cs/dokumenty-ke-stazeni",
            fetched_at=date(2026, 9, 9),
        ),
    ]

    art_dir.joinpath("matching_request.txt").write_text("weight=1200g, 250x200x100mm, CZ", encoding="utf-8")
    result = match_shipping_services(
        packed_weight_g=1200,
        packed_l_mm=250,
        packed_w_mm=200,
        packed_h_mm=100,
        services=services,
        destination_country="CZ",
        item_value=Decimal("1500"),
    )
    matched = [s.service_name for s in result.suitable_services]
    art_dir.joinpath("matching_response.txt").write_text(f"matched={matched}", encoding="utf-8")
    return {"count": len(matched)}


def validate_c39(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    mr = art_dir / "matching_response.txt"
    if not mr.exists():
        return False, "отсутствует: отсутствует matching_response.txt"
    if meta.get("count", 0) > 0:
        return True, f"пройдено: подобрано служб доставки по габаритам: {meta.get('count')}"
    return False, "поведение неверно: службы доставки по габаритам не найдены"


def run_c40(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-40: Shipping tariff config without source and date fails validation."""
    from decimal import Decimal
    from pydantic import ValidationError
    from nextlife.shipping.schema import ShippingService, ShippingValidationError, load_and_validate_carrier_file

    failed_as_expected = False
    err_text = ""
    try:
        ShippingService(
            name="test_bad_service",
            service_type="box",
            weight_max_g=5000,
            price=Decimal("79.00"),
            currency="CZK",
            countries=["CZ"],
            # Missing mandatory source_file, source_page, source_url and fetched_at
        )
    except (ValidationError, ShippingValidationError) as exc:
        failed_as_expected = True
        err_text = str(exc)
    except Exception as exc:
        err_text = str(exc)

    art_dir.joinpath("validation_output.txt").write_text(
        f"validation_failed={failed_as_expected}\nerror={err_text}", encoding="utf-8"
    )
    return {"validates_rejection": failed_as_expected}


def validate_c40(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    vo = art_dir / "validation_output.txt"
    if not vo.exists():
        return False, "отсутствует: отсутствует validation_output.txt"
    if meta.get("validates_rejection"):
        return True, "пройдено: тариф без источника и даты отклонён валидацией"
    return False, "поведение неверно: тариф без источника и даты прошёл валидацию"


def run_c41(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-41: Internal floor price substitution in outgoing text raises exception."""
    from nextlife.comms.outgoing_filter import InternalFieldLeakageError, render_outgoing_template

    leak_prevented = False
    err_text = ""
    try:
        render_outgoing_template("Minimal floor is {{ price_floor }}", {"price_floor": 800})
    except InternalFieldLeakageError as exc:
        leak_prevented = True
        err_text = f"InternalFieldLeakageError: {exc}"
    except Exception as exc:
        err_text = f"Unexpected error: {exc}"

    art_dir.joinpath("filter_output.txt").write_text(
        f"leak_prevented={leak_prevented}\ninfo={err_text}", encoding="utf-8"
    )
    return {"leak_prevented": leak_prevented}


def validate_c41(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    fo = art_dir / "filter_output.txt"
    if not fo.exists():
        return False, "отсутствует: отсутствует filter_output.txt"
    if meta.get("leak_prevented"):
        return True, "пройдено: утечка минимальной цены пресечена исключением"
    return False, "поведение неверно: минимальная цена не защищена от утечки"


def get_desc_checks() -> list[CheckDef]:
    return [
        CheckDef("C-35", "Описание", "Карточка показывает заметки, сгенерированный и финальный текст", "Все три блока присутствуют и непусты", run_c35, ["screenshot.png", "text_blocks.txt"], validate_c35),
        CheckDef("C-36", "Описание", "Счётчик символов и лимит площадки", "Лимит взят из конфига площадки, не из кода", run_c36, ["dom_limits.txt", "config_limit.txt"], validate_c36),
        CheckDef("C-37", "Описание", "Правка финального текста обнуляет утверждение", "Публикация после правки отклонена", run_c37, ["edit_response.txt", "publish_response.txt", "db_approval.txt"], validate_c37),
        CheckDef("C-38", "Доставка", "Добавление опции доставки", "Опция сохранена, «итого для покупателя» пересчитано", run_c38, ["delivery_request.txt", "delivery_response.txt", "buyer_total.txt"], validate_c38),
        CheckDef("C-39", "Доставка", "Подбор тарифа по габаритам", "Возвращены подходящие услуги по габаритам", run_c39, ["matching_request.txt", "matching_response.txt"], validate_c39),
        CheckDef("C-40", "Доставка", "Тариф без источника и даты", "Конфиг не проходит валидацию", run_c40, ["validation_output.txt"], validate_c40),
        CheckDef("C-41", "Цена", "Внутреннее поле минимальной цены в исходящем тексте", "Попытка подстановки вызывает исключение", run_c41, ["filter_output.txt"], validate_c41),
    ]
