"""Reconciliation service for verifying live marketplace listings (TZ §6.1).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import re
import urllib.request
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.status import ListingStatus
from nextlife.storage.models import Item, Listing


def check_url_is_live(url: str) -> bool:
    """Probe external marketplace listing URL to determine if it is live."""
    if not url or not url.startswith("http"):
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # Base marketplace root URL is not a listing page
        if not parsed.path or parsed.path.strip("/") in ("", "inzerat", "inzeraty"):
            return False

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            if resp.status != 200:
                return False
            html = resp.read().decode("utf-8", errors="ignore").lower()

            # Marketplace-specific checks
            if "bazos." in url:
                if any(x in html for x in [
                    "inzerát byl vymazán", "inzerat byl vymazan",
                    "inzerát bol vymazaný", "inzerat bol vymazany",
                    "požadovaný inzerát neexistuje", "inzerát neexistuje",
                    "požadovaný inzerát sa nenašiel", "hledaný inzerát nebyl nalezen",
                    "tento inzerát už nie je aktívny", "inzerát je zmazaný",
                    "chyba 404", "bazar - pc",
                ]):
                    return False
                if any(x in html for x in ["inzeratydet", "cena:", "popis:", "id inzerátu", "telefón:", "kontaktovať inzerenta", "všetky inzeráty"]):
                    return True
                return False

            if "sbazar.cz" in url:
                if any(x in html for x in ["inzerát byl odstraněn", "neexistuje", "nenalezen", "nabídka již není aktivní"]):
                    return False
                return True

            if "aukro.cz" in url:
                if any(x in html for x in ["tato nabídka skončila", "stránka nebyla nalezena", "nabídka byla ukončena"]):
                    return False
                return True

            return True
    except Exception:
        return False


def reconcile_listings_with_marketplaces(
    session: Session, item_code: str | None = None
) -> dict[str, int]:
    """Scan listings with external URLs and reconcile ACTIVE vs ARCHIVED statuses."""
    now = datetime.now(UTC)
    stmt = (
        select(Listing)
        .join(Item, Item.code == Listing.item_code)
        .where(Item.status != "DELETED")
    )
    if item_code:
        stmt = stmt.where(Listing.item_code == item_code)

    listings = session.scalars(stmt).all()
    checked = 0
    active_count = 0
    archived_count = 0

    for l in listings:
        if not l.external_url:
            # If marked ACTIVE but has no external URL, transition to ARCHIVED
            if l.status in (ListingStatus.PUBLISHED.value, "ACTIVE"):
                l.status = ListingStatus.ARCHIVED.value
                l.archived_at = now
                archived_count += 1
            continue

        checked += 1
        is_live = check_url_is_live(l.external_url)
        if is_live:
            l.status = "ACTIVE"
            l.last_seen_at = now
            if not l.published_at:
                l.published_at = now
            if not l.first_published_at:
                l.first_published_at = now
            l.last_error_text = None
            l.last_error_hint = None
            active_count += 1
        else:
            if l.status in (ListingStatus.PUBLISHED.value, "ACTIVE"):
                l.status = ListingStatus.ARCHIVED.value
                l.archived_at = now
                archived_count += 1

    session.flush()
    return {"checked": checked, "active": active_count, "archived": archived_count}
