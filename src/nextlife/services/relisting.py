"""Listing bump and relisting service (TZ v2.0 Section I.3, II.1).

DoD:
- In-place bump/update on marketplaces that support it (e.g. edit/bump capability).
- Where bump is not supported, relisting creates a new Listing record with a link to previous_listing_id instead of overwriting.
- Previous listing is marked ARCHIVED, preserving full history.
- Audit event and TruthLog assertions recorded.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Item, Listing, Lot


class RelistingServiceError(NextLifeError):
    """Base exception for listing bump and relisting operations."""


def can_bump_in_place(marketplace_key: str, config_bundle: Any | None = None) -> bool:
    """Determine whether a marketplace supports in-place bumping without creating a new ad."""
    cfg = config_bundle or load_config()
    mp_cfg = cfg.marketplaces.get(marketplace_key)
    if not mp_cfg:
        return False

    caps = getattr(mp_cfg, "capabilities", {})
    if isinstance(caps, dict):
        return bool(caps.get("bump", False) or caps.get("edit", False))
    return bool(getattr(caps, "bump", False) or getattr(caps, "edit", False))


def bump_or_relist_listing(
    session: Session,
    listing_id: str,
    *,
    new_price: Decimal | None = None,
    connector: Any | None = None,
    force_relist: bool = False,
    actor: str = "AGENT",
    config_bundle: Any | None = None,
) -> dict[str, Any]:
    """Perform in-place bump or relist with previous_listing_id link.

    DoD:
    - If supported and not force_relist: bumps listing in-place.
    - If bump not supported or force_relist: archives existing listing, creates a new
      Listing with previous_listing_id link, without overwriting old record.

    Ч-5 (TZ M1): both paths are real site actions — the connector performs
    them first; the service never fabricates external id/url and never
    touches DB rows before the live action succeeds.
    """
    listing = session.get(Listing, listing_id)
    if not listing:
        raise RelistingServiceError(f"Listing '{listing_id}' not found")

    item = session.scalar(select(Item).where(Item.code == listing.item_code))
    if not item:
        raise RelistingServiceError(f"Item '{listing.item_code}' not found")

    lot = session.scalar(select(Lot).where(Lot.item_id == item.code))
    lot_code = lot.code if lot else item.code

    now_utc = datetime.now(UTC)
    mp_key = listing.marketplace_key
    supports_bump = can_bump_in_place(mp_key, config_bundle) and not force_relist

    if supports_bump:
        # IN-PLACE BUMP / UPDATE: live site action first (Ч-5).
        if connector is None:
            raise RelistingServiceError(
                f"bump on '{mp_key}' requires a connector to perform the live update"
            )
        if hasattr(connector, "bump"):
            connector.bump(listing.external_id)
        elif hasattr(connector, "update"):
            connector.update(listing.external_id, {"price": float(new_price or listing.price)})
        else:
            raise RelistingServiceError(
                f"connector for '{mp_key}' has no bump/update method; in-place bump not possible"
            )

        old_price = listing.price
        if new_price is not None:
            listing.price = new_price
            listing.price_base = new_price
        listing.published_at = now_utc
        listing.last_seen_at = now_utc

        session.flush()

        record_truth(
            session=session,
            claim=f"Объявление лота #{lot_code} на площадке {mp_key} успешно поднято на месте.",
            recipient="OWNER",
            evidence={
                "listing_id": listing.id,
                "action": "BUMPED",
                "old_price": str(old_price),
                "new_price": str(listing.price),
            },
        )
        record_audit_event(
            session=session,
            actor=actor,
            action="LISTING_BUMPED",
            entity="listing",
            entity_id=listing.id,
            data={
                "in_place": True,
                "marketplace": mp_key,
                "new_price": str(listing.price),
                "item_code": item.code,
            },
        )

        return {
            "action": "BUMPED",
            "in_place": True,
            "listing_id": listing.id,
            "previous_listing_id": listing.previous_listing_id,
            "marketplace_key": mp_key,
            "price": listing.price,
            "status": listing.status,
        }

    else:
        # RELIST: a real re-publication (Ч-5) — performed by the connector.
        # Without it no new external id/url is fabricated.
        if connector is None:
            raise RelistingServiceError(
                f"relist on '{mp_key}' requires a connector to perform the live re-publication"
            )
        if not hasattr(connector, "publish"):
            raise RelistingServiceError(
                f"connector for '{mp_key}' has no publish method; relist not possible"
            )

        pub_res = connector.publish(
            {"title": item.title, "price": float(new_price or listing.price)}
        )
        old_listing_id = listing.id
        listing.status = "ARCHIVED"
        listing.archived_at = now_utc

        new_ext_id = pub_res.external_id
        new_ext_url = pub_res.external_url
        new_evidence = dict(pub_res.evidence or {})
        new_evidence.setdefault("relisted_from", old_listing_id)

        price_to_use = new_price if new_price is not None else listing.price
        new_listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item.code,
            marketplace_key=mp_key,
            external_id=new_ext_id,
            external_url=new_ext_url,
            price=price_to_use,
            currency=listing.currency,
            price_base=price_to_use,
            status="PUBLISHED",
            published_at=now_utc,
            publish_evidence=new_evidence,
            previous_listing_id=old_listing_id,  # MANDATORY LINK TO PREVIOUS LISTING!
        )
        session.add(new_listing)
        session.flush()

        record_truth(
            session=session,
            claim=f"Объявление лота #{lot_code} на площадке {mp_key} переразмещено. Создано новое объявление #{new_listing.id} со ссылкой на #{old_listing_id}.",
            recipient="OWNER",
            evidence={
                "new_listing_id": new_listing.id,
                "previous_listing_id": old_listing_id,
                "action": "RELISTED",
            },
        )
        record_audit_event(
            session=session,
            actor=actor,
            action="LISTING_RELISTED",
            entity="listing",
            entity_id=new_listing.id,
            data={
                "in_place": False,
                "marketplace": mp_key,
                "previous_listing_id": old_listing_id,
                "new_price": str(price_to_use),
                "item_code": item.code,
            },
        )

        return {
            "action": "RELISTED",
            "in_place": False,
            "listing_id": new_listing.id,
            "previous_listing_id": old_listing_id,
            "marketplace_key": mp_key,
            "price": new_listing.price,
            "status": new_listing.status,
        }


def get_listing_lineage(
    session: Session,
    listing_id: str,
) -> list[dict[str, Any]]:
    """Retrieve full ancestor lineage of a listing through previous_listing_id chain."""
    lineage = []
    curr_id: str | None = listing_id

    while curr_id:
        lst = session.get(Listing, curr_id)
        if not lst:
            break
        lineage.append(
            {
                "id": lst.id,
                "status": lst.status,
                "price": lst.price,
                "published_at": lst.published_at,
                "archived_at": lst.archived_at,
                "previous_listing_id": lst.previous_listing_id,
            }
        )
        curr_id = lst.previous_listing_id

    return lineage
