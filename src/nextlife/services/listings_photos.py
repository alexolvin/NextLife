"""Service for managing item photos, cover designation, and listing propagation.

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.status import ListingStatus
from nextlife.media.pipeline import display_photo_for_chain, logical_photo_chains
from nextlife.obs.audit import record_audit_event
from nextlife.storage.models import Item, ItemPhoto, Listing


def get_item_ordered_photo_ids(session: Session, item_code: str) -> list[str]:
    """Return ordered list of display photo IDs for an item, with cover photo first."""
    chains = logical_photo_chains(session, item_code)
    return [display_photo_for_chain(c).id for c in chains]


def propagate_cover_photo_to_listings(session: Session, item_code: str) -> list[str]:
    """Update all non-archived listings for item so that photo_ids starts with cover photo."""
    photo_ids = get_item_ordered_photo_ids(session, item_code)
    if not photo_ids:
        return []

    listings = session.scalars(
        select(Listing).where(
            Listing.item_code == item_code,
            Listing.status.notin_([ListingStatus.ARCHIVED.value, "DELETED"]),
        )
    ).all()

    for lst in listings:
        lst.photo_ids = photo_ids

    session.flush()
    return photo_ids


def set_item_cover_photo(
    session: Session,
    item_code: str,
    photo_id: str,
    *,
    actor: str = "USER",
) -> dict[str, Any]:
    """Designate a photo as the main/cover photo and propagate to all active listings."""
    item = session.get(Item, item_code)
    if not item:
        raise ValueError(f"Item {item_code} not found")

    photo = session.get(ItemPhoto, photo_id)
    if not photo or photo.item_code != item_code:
        raise ValueError(f"Photo {photo_id} not found for item {item_code}")

    attrs = dict(item.attrs or {})
    attrs["cover_photo_id"] = photo_id

    existing_order = [p for p in (attrs.get("photo_order") or []) if p != photo_id]
    attrs["photo_order"] = [photo_id] + existing_order
    item.attrs = attrs

    if isinstance(photo.transform_log, list):
        photo.transform_log = list(photo.transform_log) + [{"step": "set_cover"}]
    elif isinstance(photo.transform_log, dict):
        t_log = dict(photo.transform_log)
        t_log["is_cover"] = True
        photo.transform_log = t_log
    else:
        photo.transform_log = [{"step": "set_cover"}]

    propagated_photo_ids = propagate_cover_photo_to_listings(session, item_code)

    record_audit_event(
        session=session,
        actor=actor,
        action="PHOTO_SET_COVER",
        entity="ITEM",
        entity_id=item_code,
        data={"photo_id": photo_id, "cover_photo_ids": propagated_photo_ids},
    )
    session.flush()

    return {
        "status": "ok",
        "item_code": item_code,
        "cover_photo_id": photo_id,
        "propagated_count": len(propagated_photo_ids),
    }
