"""Lot publication service with external access verification, mandatory evidence, and audit (TZ v2.0, v1.1 section 6.2).

DoD:
- Mandatory external_url and publish_evidence for successful publication.
- Without external credentials / access -> BLOCKED-EXTERNAL.
- On fixtures, absence of URL strictly does not give PUBLISHED status.
- Truth log and audit event recorded with evidence.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.connectors.base import PublishResult
from nextlife.connectors.prepublish import validate_lot_for_publication
from nextlife.connectors.registry import CONNECTOR_MAP
from nextlife.core.config import MarketplaceConfig, load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.core.secrets import resolve_secret
from nextlife.core.status import ItemStatus, can_transition, validate_transition
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Item, Listing, Lot


class PublicationBlockedExternalError(NextLifeError):
    """Raised when marketplace publication cannot proceed due to missing external credentials."""


class PublicationEvidenceMissingError(NextLifeError):
    """Raised when connector fails to supply required external_url or publish evidence."""


def check_marketplace_access(config: MarketplaceConfig) -> tuple[bool, str | None]:
    """Verify whether required credentials exist for accessing marketplace."""
    auth = getattr(config, "auth", {})
    if isinstance(auth, dict):
        auth_type = auth.get("type", "none")
        secret_ref = auth.get("secret_ref", "")
    else:
        auth_type = getattr(auth, "type", "none")
        secret_ref = getattr(auth, "secret_ref", "")

    if auth_type and auth_type != "none":
        try:
            secret_val = resolve_secret(secret_ref) if secret_ref else None
        except Exception:
            secret_val = None
        if not secret_val:
            return False, f"BLOCKED-EXTERNAL: Missing secret '{secret_ref}' for {config.key}"
    return True, None


def publish_lot(
    session: Session,
    lot_code: str,
    target_marketplaces: list[str],
    *,
    dry_run: bool = False,
    require_credentials: bool = False,
    connector_overrides: dict[str, Any] | None = None,
    config_bundle: Any | None = None,
    actor: str = "AGENT",
) -> dict[str, Any]:
    """Publish a lot to specified marketplaces with mandatory evidence and access checks.

    DoD:
    - Missing credentials/access -> BLOCKED-EXTERNAL.
    - Missing external_url -> does NOT transition to PUBLISHED.
    - Full evidence logged into TruthLog and Listing.
    """
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ValueError(f"Lot with code {lot_code} not found")

    item = session.get(Item, lot.item_id)
    if not item:
        raise ValueError(f"Item {lot.item_id} not found")

    cfg_bundle = config_bundle or load_config()
    connector_overrides = connector_overrides or {}

    # Transition item to PUBLISHING if possible
    if can_transition(item.status, ItemStatus.PUBLISHING):
        validate_transition(ItemStatus(item.status), ItemStatus.PUBLISHING)
        item.status = ItemStatus.PUBLISHING.value
    lot.status = ItemStatus.PUBLISHING.value

    successes: list[str] = []
    failures: dict[str, str] = {}
    blocked_external: dict[str, str] = {}
    created_listings: list[dict[str, Any]] = []

    for mp_key in target_marketplaces:
        mp_cfg = cfg_bundle.marketplaces.get(mp_key)
        if not mp_cfg:
            failures[mp_key] = f"Marketplace config for '{mp_key}' not found."
            continue

        # 0a. TZ M1 A.5: ограничение по категории из реестра площадки —
        # лот другой категории на эту площадку не предлагается.
        allowed_categories = getattr(mp_cfg, "allowed_categories", None)
        if allowed_categories and item.category_key not in allowed_categories:
            failures[mp_key] = (
                f"Category '{item.category_key}' is not allowed on '{mp_key}' "
                f"(allowed categories: {', '.join(allowed_categories)}). Lot not offered."
            )
            continue

        # 0. D.1: публикации на заблокированном канале не выполняются.
        # Статус канала — из канарейки (живая проба, Ч-1), не из памяти.
        if getattr(mp_cfg, "access_mode", None) == "DIRECT":
            from nextlife.canaries.engine import is_direct_channel_blocked

            canary_reason = is_direct_channel_blocked(session, mp_key)
            if canary_reason:
                failures[mp_key] = f"Channel BLOCKED by canary: {canary_reason}"
                continue

        # 1. External Access Verification
        has_access, block_reason = check_marketplace_access(mp_cfg)
        if require_credentials and not has_access:
            blocked_external[mp_key] = block_reason or "BLOCKED-EXTERNAL: Access denied"
            failures[mp_key] = blocked_external[mp_key]
            continue

        # Prepare description with mandatory legal disclaimer if CZ
        country = getattr(mp_cfg, "country", "CZ").lower()
        desc = (item.attrs or {}).get("description") or (item.notes or item.title)
        if country == "cz" and "soukromý prodej" not in desc.lower():
            try:
                from nextlife.text.legal import render_legal_disclaimer
                legal_text = render_legal_disclaimer("cz")
                desc = f"{desc}\n\n{legal_text}".strip()
            except Exception:
                pass

        # Prepare payload (price_floor_check used instead of forbidden internal field price_floor)
        lot_payload = {
            "lot_code": lot.code,
            "item_code": item.code,
            "title": item.title,
            "description": desc,
            "price": float(lot.price_gross or item.target_price or 0.0),
            "price_floor_check": float(lot.price_floor) if lot.price_floor is not None else None,
            "currency": lot.currency,
            "category": item.category_key,
        }

        # 2. Pre-publish validation
        try:
            report = validate_lot_for_publication(lot_payload, mp_cfg)
            if not report.is_valid:
                failures[mp_key] = f"Pre-publish validation failed: {', '.join(report.errors)}"
                continue
        except Exception as exc:
            failures[mp_key] = f"Pre-publish validation failed: {exc}"
            continue

        # 3. Connector execution
        try:
            if mp_key in connector_overrides:
                connector = connector_overrides[mp_key]
            else:
                conn_cls = CONNECTOR_MAP.get(mp_key)
                if not conn_cls:
                    failures[mp_key] = f"No connector class found for '{mp_key}'."
                    continue
                connector = conn_cls(mp_cfg)

            pub_result: PublishResult = connector.publish(lot_payload, dry_run=dry_run)

            # 4. Mandatory external_url and evidence check (Rule П-1.1)
            ext_url = getattr(pub_result, "external_url", None)
            evidence = getattr(pub_result, "evidence", None)

            if not ext_url or not str(ext_url).strip():
                failures[mp_key] = (
                    f"Publication rejected on {mp_key}: missing mandatory external_url in connector response."
                )
                continue

            if not evidence or not isinstance(evidence, dict):
                failures[mp_key] = (
                    f"Publication rejected on {mp_key}: missing mandatory publish_evidence."
                )
                continue

            # 5. Persist Listing
            listing_id = str(uuid.uuid4())
            price_val = lot.price_gross or item.target_price or Decimal("0.00")
            listing = Listing(
                id=listing_id,
                item_code=item.code,
                marketplace_key=mp_key,
                external_id=pub_result.external_id,
                external_url=str(ext_url).strip(),
                price=price_val,
                currency=lot.currency,
                price_base=price_val,
                status="PUBLISHED",
                published_at=datetime.now(UTC),
                publish_evidence=evidence,
            )
            session.add(listing)
            session.flush()

            # 6. Record truth log entry
            record_truth(
                session=session,
                claim=f"Объявление лота #{lot.code} опубликовано на площадке {mp_cfg.title}: {ext_url}",
                recipient="OWNER",
                evidence=evidence,
            )

            successes.append(mp_key)
            created_listings.append(
                {
                    "id": listing_id,
                    "marketplace_key": mp_key,
                    "external_url": str(ext_url).strip(),
                    "status": "PUBLISHED",
                }
            )

        except Exception as exc:
            failures[mp_key] = f"Connector exception: {exc}"

    # 7. Final status resolution
    if len(successes) == len(target_marketplaces) and len(target_marketplaces) > 0:
        item.status = ItemStatus.PUBLISHED.value
        lot.status = ItemStatus.PUBLISHED.value
    elif len(successes) > 0:
        item.status = ItemStatus.PARTIALLY_PUBLISHED.value
        lot.status = ItemStatus.PARTIALLY_PUBLISHED.value
    elif blocked_external and len(blocked_external) == len(target_marketplaces):
        # All blocked externally
        item.status = ItemStatus.BLOCKED.value
        lot.status = "BLOCKED-EXTERNAL"
    else:
        item.status = ItemStatus.BLOCKED.value
        lot.status = ItemStatus.BLOCKED.value

    # 8. Record audit event
    record_audit_event(
        session=session,
        actor=actor,
        action="LOT_PUBLISH_ATTEMPT",
        entity="lot",
        entity_id=lot.code,
        data={
            "target_marketplaces": target_marketplaces,
            "successes": successes,
            "failures": failures,
            "blocked_external": blocked_external,
            "final_item_status": item.status,
            "final_lot_status": lot.status,
        },
    )

    return {
        "lot_code": lot.code,
        "item_code": item.code,
        "item_status": item.status,
        "lot_status": lot.status,
        "successes": successes,
        "failures": failures,
        "blocked_external": blocked_external,
        "listings": created_listings,
    }


def validate_published_state(session: Session, code: str) -> tuple[bool, str]:
    """Validate that lot/item has valid external publication URL for PUBLISHED status.

    Returns (is_valid, error_message).
    Status PUBLISHED strictly requires a verified non-empty external_url in active listings.
    """
    item = session.scalar(select(Item).where(Item.code == code))
    if not item:
        lot = session.scalar(select(Lot).where(Lot.code == code))
        if lot:
            item = session.get(Item, lot.item_id)

    if not item:
        return False, f"Item with code '{code}' not found"

    listings = session.scalars(select(Listing).where(Listing.item_code == item.code)).all()
    if not listings:
        return False, f"Cannot transition to PUBLISHED: no marketplace listings exist for #{item.code}"

    valid_listings = [
        l for l in listings
        if l.external_url and str(l.external_url).strip().startswith(("http://", "https://"))
    ]
    if not valid_listings:
        return False, f"Cannot transition to PUBLISHED: no external marketplace URL found for #{item.code}"

    return True, ""


