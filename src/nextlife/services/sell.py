"""Selling scenario service: draft building, approval validation, publication, and queueing (sections 9, 13, 17)."""

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.connectors.base import BaseConnector, PublishResult
from nextlife.core.approval import compute_payload_hash, has_valid_approval
from nextlife.core.config import ConfigBundle, load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.core.internal_fields import assert_outgoing_clean
from nextlife.core.status import ItemStatus, validate_transition
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Item, Job, Listing, Marketplace


class SellServiceError(NextLifeError):
    """Base error for selling service."""


class MissingApprovalError(SellServiceError):
    """Raised when publication is attempted without valid approval or with modified payload."""


class PublicationEvidenceMissingError(SellServiceError):
    """Raised when marketplace result lacks external_url or valid evidence."""


class AndroidDeviceUnavailableError(SellServiceError):
    """Raised when Android bridge device is offline or unavailable."""


def build_draft(
    session: Session,
    item_code: str,
    config: ConfigBundle | None = None,
) -> dict[str, Any]:
    """Generate comprehensive draft object for item (section 9.2, Ш4)."""
    item = session.get(Item, item_code)
    if not item:
        raise SellServiceError(f"Item not found: {item_code}")

    cfg = config or load_config()

    # Determine eligible marketplaces from DB/config
    stmt = select(Marketplace)
    db_mps = {mp.key: mp for mp in session.execute(stmt).scalars().all()}

    eligible_marketplaces: list[str] = []
    excluded_marketplaces: dict[str, str] = {}

    for key, mp_cfg in cfg.marketplaces.items():
        # TZ Marketplaces: phone-broker marketplaces (ANDROID) are a separate
        # channel (publish via the phone connector, Phase 2) — not part of the
        # web draft/publish path. Exception: a marketplace with
        # publish_channel=web (e.g. Vinted, owner decision 2026-10-10) publishes
        # via the web connector while its search stays on the phone channel.
        if mp_cfg.access_mode == "ANDROID" and mp_cfg.publish_channel != "web":
            excluded_marketplaces[key] = "android_channel"
            continue
        db_mp = db_mps.get(key)
        if db_mp and not db_mp.enabled:
            excluded_marketplaces[key] = "marketplace_disabled"
            continue
        eligible_marketplaces.append(key)

    # Shipping options from config
    shipping_info = {
        "pickup_allowed": item.attrs.get("pickup_allowed", True),
        "methods": [
            {
                "key": m.key,
                "title": m.title,
                "cost_base": m.cost_base,
                "currency": m.currency,
                "allowed_countries": m.allowed_countries,
            }
            for m in cfg.shipping.methods
        ],
    }

    # TZ-NextLife-UI-N2 §1.3: умолчание EUR удалено — отсутствие
    # валюты предмета является ошибкой, а не поводом подставлять
    # отчётную валюту.
    if not item.target_currency:
        raise SellServiceError(
            f"У предмета {item_code} не задана валюта (target_currency) — "
            "черновик создаётся только с явной валютой."
        )

    # Draft payload structure.
    # ТЗ «Склад» §1.3.3: payload включает title, description, description_lang,
    # usage, specs, target_price, target_currency — любое изменение этих полей
    # меняет payload_hash и делает утверждение недействительным.
    draft_payload: dict[str, Any] = {
        "item_code": item.code,
        "title": item.title,
        "description": item.description,
        "description_lang": item.description_lang,
        "usage": item.usage,
        "specs": item.specs,
        "category_key": item.category_key,
        "price": str(item.target_price or Decimal("0.00")),
        "currency": item.target_currency,
        "condition": item.condition_key,
        "defects": list(item.defects.keys())
        if isinstance(item.defects, dict)
        else list(item.defects),
        "shipping": shipping_info,
        "eligible_marketplaces": sorted(eligible_marketplaces),
        "excluded_marketplaces": excluded_marketplaces,
    }

    from nextlife.services.draft_text import get_item_text_blocks, is_cyrillic_dominant
    text_blocks = get_item_text_blocks(item)
    draft_payload["body"] = text_blocks["final_text"]

    # TZ-NextLife-UI-N2 §1.2: техническая гарантия — внутренние поля
    # (price_base и пр.) не проходят барьер сериализации.
    assert_outgoing_clean(draft_payload)

    payload_hash = compute_payload_hash(draft_payload)
    draft_payload["payload_hash"] = payload_hash

    # TZ-NextLife-UI-N2 §3.3 P-E03: проверка языка. Русский текст при
    # целевых чешских площадках — блокирующее предупреждение. Маркер
    # добавлен ПОСЛЕ хэша: это служебный индикатор безопасности, а не
    # публикуемое содержимое, поэтому он не меняет payload_hash.
    cz_targets = [k for k in draft_payload["eligible_marketplaces"] if k.endswith("_cz")]
    if cz_targets and is_cyrillic_dominant(draft_payload["body"]):
        draft_payload["language_warning"] = (
            "Текст объявления написан на русском языке, а цель — чешская "
            "площадка. Публикация заблокирована до перевода текста на чешский."
        )
    return draft_payload


def publish_item(
    session: Session,
    item_code: str,
    draft_payload: dict[str, Any],
    connectors: dict[str, BaseConnector] | None = None,
    config: ConfigBundle | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Publish approved draft to target marketplaces with idempotency and truth tracking (section 9.2 Ш5)."""
    # Acceptance kill switch (owner, 2026-10-04): NEXTLIFE_PUBLISH_DRY_RUN=1 on the
    # server process forces dry_run on every publish — real marketplace publication
    # is impossible even if the approval gate were to fail.
    dry_run = dry_run or os.environ.get("NEXTLIFE_PUBLISH_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    item = session.get(Item, item_code)
    if not item:
        raise SellServiceError(f"Item not found: {item_code}")

    cfg = config or load_config()

    # 1. Verification of draft approval (Rule П-1.1, Section 17)
    payload_hash = compute_payload_hash(
        {k: v for k, v in draft_payload.items() if k != "payload_hash"}
    )
    if not has_valid_approval(session, item_code, payload_hash):
        raise MissingApprovalError(
            f"Cannot publish item {item_code}: draft payload has not been approved or was modified."
        )

    # Ensure status can transition to PUBLISHING
    current_status = ItemStatus(item.status)
    if current_status in (ItemStatus.APPROVED, ItemStatus.PARTIALLY_PUBLISHED):
        validate_transition(current_status, ItemStatus.PUBLISHING)
        item.status = ItemStatus.PUBLISHING.value
        session.flush()

    target_marketplaces = draft_payload.get("eligible_marketplaces", [])
    successes: list[str] = []
    failures: dict[str, str] = {}

    for mp_key in target_marketplaces:
        idempotency_key = f"{item_code}_{mp_key}_{payload_hash}"

        # 2. Check if already published or job already succeeded (Idempotency)
        existing_listing = session.execute(
            select(Listing).where(
                Listing.item_code == item_code,
                Listing.marketplace_key == mp_key,
                Listing.status == "ACTIVE",
            )
        ).scalar_one_or_none()

        if existing_listing:
            successes.append(mp_key)
            continue

        mp_cfg = cfg.marketplaces.get(mp_key)
        if not mp_cfg:
            failures[mp_key] = f"Marketplace {mp_key} configuration not found."
            continue

        # Record publication Job in queue
        job = Job(
            id=str(uuid.uuid4()),
            kind="PUBLISH",
            payload={"item_code": item_code, "marketplace": mp_key, "payload_hash": payload_hash},
            state="PROCESSING",
            idempotency_key=idempotency_key,
        )
        session.add(job)
        session.flush()

        # D.1: публикации на заблокированном канале не выполняются.
        # Канарейка DIRECT-канала (живая проба) — источник статуса (Ч-1).
        if mp_cfg.access_mode == "DIRECT":
            from nextlife.canaries.engine import is_direct_channel_blocked

            blocked_reason = is_direct_channel_blocked(session, mp_key)
            if blocked_reason:
                failures[mp_key] = f"Channel BLOCKED by canary: {blocked_reason}"
                job.state = "BLOCKED"
                job.last_error = failures[mp_key]
                continue

        # Handle ANDROID access mode check. Exception: a marketplace with
        # publish_channel=web (e.g. Vinted, owner decision 2026-10-10) publishes
        # via the web connector while its search stays on the phone channel.
        if mp_cfg.access_mode == "ANDROID" and mp_cfg.publish_channel != "web":
            failures[mp_key] = (
                "Android phone unavailable: устройство не отвечает или агент отключен."
            )
            job.state = "BLOCKED"
            job.last_error = failures[mp_key]
            continue

        if dry_run:
            # Dry-run reaches the connector when one is provided (e.g. Vinted's
            # structured no-op publish-flow); otherwise a generic dry result.
            dry_connector = connectors.get(mp_key) if connectors else None
            if dry_connector is not None:
                pub_result = dry_connector.publish(draft_payload, dry_run=True)
            else:
                pub_result = PublishResult(
                    external_id=f"dry_{uuid.uuid4().hex[:8]}",
                    external_url=f"{mp_cfg.base_url.rstrip('/')}/item/{item_code}",
                    evidence={"dry_run": True, "ts": datetime.now(UTC).isoformat()},
                )
        else:
            connector = connectors.get(mp_key) if connectors else None
            if not connector:
                failures[mp_key] = f"No connector available for {mp_key}"
                job.state = "FAILED"
                job.last_error = failures[mp_key]
                continue

            try:
                if hasattr(connector, "publish"):
                    try:
                        pub_result = connector.publish(draft_payload, dry_run=False)
                    except TypeError:
                        pub_result = connector.publish(item_code, draft_payload)  # type: ignore[arg-type]
                else:
                    failures[mp_key] = f"Connector {mp_key} does not support direct publishing."
                    job.state = "FAILED"
                    job.last_error = failures[mp_key]
                    continue
            except Exception as exc:
                failures[mp_key] = f"Connector error on {mp_key}: {exc}"
                job.state = "FAILED"
                job.last_error = failures[mp_key]
                continue

        # Rule П-1.1: external_url and evidence are mandatory
        if not pub_result.external_url or not pub_result.evidence:
            failures[mp_key] = (
                "Publication rejected: external_url or evidence missing from connector response."
            )
            job.state = "FAILED"
            job.last_error = failures[mp_key]
            continue

        # TZ-NextLife-UI-N2 §1.3: валюту листинга не выдумываем —
        # только из payload черновика (там она обязательна).
        listing_currency = draft_payload.get("currency")
        if not listing_currency:
            failures[mp_key] = (
                "Публикация отклонена: валюта отсутствует в payload черновика."
            )
            job.state = "FAILED"
            job.last_error = failures[mp_key]
            continue

        # Create active listing
        new_listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key=mp_key,
            external_id=pub_result.external_id,
            external_url=pub_result.external_url,
            price=Decimal(str(draft_payload.get("price", "0.00"))),
            currency=str(listing_currency),
            price_base=Decimal(str(draft_payload.get("price", "0.00"))),
            status="ACTIVE",
            published_at=datetime.now(UTC),
            publish_evidence=pub_result.evidence,
        )
        session.add(new_listing)
        job.state = "SUCCESS"
        successes.append(mp_key)

        # Record truth log for verified publication
        record_truth(
            session=session,
            claim=f"Объявление размещено на площадке {mp_key}: {pub_result.external_url}",
            recipient="OWNER",
            evidence=pub_result.evidence,
        )

    # Determine final item status (Section 9.2 Ш5, Rule П-1.4)
    if len(successes) == len(target_marketplaces) and len(target_marketplaces) > 0:
        validate_transition(ItemStatus.PUBLISHING, ItemStatus.PUBLISHED)
        item.status = ItemStatus.PUBLISHED.value
    elif len(successes) > 0:
        validate_transition(ItemStatus.PUBLISHING, ItemStatus.PARTIALLY_PUBLISHED)
        item.status = ItemStatus.PARTIALLY_PUBLISHED.value
    else:
        validate_transition(ItemStatus.PUBLISHING, ItemStatus.BLOCKED)
        item.status = ItemStatus.BLOCKED.value

    record_audit_event(
        session=session,
        actor="AGENT",
        action="PUBLISH_ATTEMPT",
        entity="item",
        entity_id=item_code,
        data={
            "status": item.status,
            "successes": successes,
            "failures": failures,
            "dry_run": dry_run,
        },
    )
    session.flush()

    return {
        "status": item.status,
        "successes": successes,
        "failures": failures,
        "dry_run": dry_run,
    }
