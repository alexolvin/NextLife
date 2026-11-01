"""Channel canary engine (TZ M1 D.1, Ч-1..Ч-3).

Daily live probe per enabled DIRECT search channel:

- raw response is saved as an artifact (Ч-1: status only from a live probe);
- extracted field schema is checked: title, price, currency, link, date;
- protection page → BLOCKED (Ч-3), never «0 results»;
- redirect to an unknown address → BLOCKED with the final URL;
- empty answer where the previous probe had results → BLOCKED;
- on transition to BLOCKED the owner is notified (outbox) with the artifact;
- publishing on a blocked channel is refused (D.1).
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.connectors.base import ConnectorConfigError, RawListing
from nextlife.connectors.registry import get_connector
from nextlife.core.config import ConfigBundle, load_config
from nextlife.storage.models import ChannelCanary

if TYPE_CHECKING:
    pass

DEFAULT_PROBE_DIR = Path(".a/reports/canaries")

# Ч-3: captcha / challenge / identity-check markers. A page containing any of
# these is protection, not an empty search result. The captcha markers match
# challenge *markup* (widget divs), not script-include URLs — result pages may
# legitimately load a captcha script without being a challenge page
# (live-verified 2026-10-04: annonce.cz includes google.com/recaptcha/api.js
# on ordinary search results).
PROTECTION_MARKERS: tuple[str, ...] = (
    "datadome",
    "data-dome",
    "g-recaptcha",
    "h-captcha",
    "px-captcha",
    "cf-challenge",
    "challenge-platform",
    "cf-turnstile",
    "just a moment",
    "ověřte, že jste člověk",
    "ověření, že nejste robot",
)

_SCENARIO = "search"
_DIRECT = "DIRECT"


@dataclass(frozen=True)
class CanaryResult:
    key: str
    scenario: str
    channel: str
    state: str  # OK | BLOCKED
    reason: str | None
    artifact_path: str | None
    items_count: int
    final_url: str


def _route_static_prefix(route: str) -> str:
    """Static part of a route template: '/hledej/{query}' -> '/hledej/'."""
    cut = route.find("{")
    return route[:cut] if cut != -1 else route


def detect_protection_marker(html: str) -> str | None:
    """Return the first protection marker found in the page (Ч-3)."""
    low = html.lower()
    for marker in PROTECTION_MARKERS:
        if marker in low:
            return marker
    return None


def _save_artifact(probe_dir: Path, key: str, raw: str, meta: dict[str, Any], raw_ext: str = "html") -> Path:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    stamp = datetime.now(UTC).strftime("%H%M%S")
    target_dir = probe_dir / day
    target_dir.mkdir(parents=True, exist_ok=True)
    raw_path = target_dir / f"{key}_{stamp}.{raw_ext}"
    raw_path.write_text(raw, encoding="utf-8")
    meta_path = target_dir / f"{key}_{stamp}.json" if raw_ext == "html" else target_dir / f"{key}_{stamp}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return raw_path


def _check_schema(items: list[RawListing], date_absent_on_site: bool) -> str | None:
    """D.1 schema check: title, price, currency, link, date. Returns reason or None."""
    for item in items:
        if not item.title or not item.title.strip():
            return "schema_drift: field=title (empty)"
        if item.price is None or item.price <= 0:
            return "schema_drift: field=price (missing or not positive)"
        if not (isinstance(item.currency, str) and len(item.currency) == 3):
            return "schema_drift: field=currency (not a 3-letter code)"
        if not (item.external_url or "").startswith(("http://", "https://")):
            return "schema_drift: field=link (not an absolute URL)"
        if date_absent_on_site and item.posted_at is not None:
            return "schema_drift: field=date (site cards do not expose a date, parser returned one)"
        if not date_absent_on_site and item.posted_at is None:
            return "schema_drift: field=date (missing)"
    return None


def run_search_canary(
    session: Session,
    key: str,
    *,
    config: ConfigBundle | None = None,
    http_client: httpx.Client | None = None,
    probe_dir: Path | None = None,
) -> CanaryResult:
    """Run one live probe for the DIRECT search channel of a marketplace."""
    cfg = config or load_config()
    mp_cfg = cfg.marketplaces[key]
    channel = mp_cfg.access_mode
    if channel != _DIRECT:
        raise ConnectorConfigError(
            f"Canary engine covers DIRECT search channels; '{key}' is {channel}."
        )

    query = mp_cfg.canary_query or "notebook"
    connector = get_connector(key, cfg, client=http_client)
    search_mode = mp_cfg.search_mode  # "html" (default) | "json" (Aukro, M1 A.3)

    probe = connector.probe_search(query)
    requested_url = probe.requested_url
    final_url = probe.final_url
    html = probe.raw_text
    http_status = probe.http_status
    network_error = probe.network_error

    probe_dir = probe_dir or DEFAULT_PROBE_DIR
    meta: dict[str, Any] = {
        "key": key,
        "scenario": _SCENARIO,
        "channel": channel,
        "query": query,
        "search_mode": search_mode,
        "requested_url": requested_url,
        "final_url": final_url,
        "http_status": http_status,
        "network_error": network_error,
        "probed_at": datetime.now(UTC).isoformat(),
    }
    artifact_path = _save_artifact(
        probe_dir,
        key,
        html or f"<!-- network_error: {network_error} -->",
        meta,
        raw_ext="json" if search_mode == "json" else "html",
    )

    reason: str | None = None
    items: list[RawListing] = []
    if network_error is not None:
        reason = f"network_error: {network_error}"
    elif http_status != 200:
        reason = f"http_{http_status}"
    else:
        # Ч-3 nuance (live-verified 2026-10-04, TrhKnih): a page that
        # actually contains the site's result records (matched > 0) is a
        # result page, not a challenge interstitial — interstitials never
        # carry the site's cards. Ordinary TrhKnih result pages inline the
        # Cloudflare real-user beacon whose URL contains the substring
        # 'challenge-platform', which must not BLOCK the channel.
        marker = detect_protection_marker(html)
        if marker and not probe.matched:
            reason = f"protection_page: marker={marker}"
        else:
            base_parts = urllib.parse.urlsplit(mp_cfg.base_url)
            final_parts = urllib.parse.urlsplit(final_url)
            route = str((mp_cfg.routes or {}).get("search", ""))
            prefix = _route_static_prefix(route)
            if (
                final_parts.netloc != base_parts.netloc
                or not final_parts.path.startswith(prefix)
            ):
                reason = f"unknown_redirect: final_url={final_url}"
            else:
                items = probe.items
                if not items:
                    # Distinguish «records present but unparsable» (schema drift,
                    # e.g. broken price selector — mutation D.3-1 must name the
                    # field) from «truly empty result».
                    if probe.matched:
                        reason = (
                            f"schema_drift: field=price "
                            f"({probe.matched} card(s) matched, 0 parsable)"
                        )
                    else:
                        row = _get_row(session, key)
                        if row and (row.last_success_count or 0) > 0:
                            reason = "empty_after_success"
                        else:
                            reason = "empty_first_probe"
                else:
                    date_absent = probe.date_absent_on_site
                    reason = _check_schema(items, date_absent)

    state = "OK" if reason is None else "BLOCKED"
    meta["state"] = state
    meta["reason"] = reason
    meta["items_count"] = len(items)
    meta_name = "json" if search_mode == "html" else "meta.json"
    artifact_path.parent.joinpath(
        artifact_path.name.rsplit(".", 1)[0] + "." + meta_name
    ).write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    prev = _get_row(session, key)
    row = prev or ChannelCanary(
        marketplace_key=key, scenario=_SCENARIO, channel=channel, probe_query=query
    )
    was_blocked = prev is not None and prev.state == "BLOCKED"
    row.probe_query = query
    row.date_absent_on_site = probe.date_absent_on_site
    row.last_attempt_at = datetime.now(UTC)
    row.state = state
    row.block_reason = reason
    row.artifact_path = str(artifact_path)
    if state == "OK":
        row.last_success_at = datetime.now(UTC)
        row.last_success_count = len(items)
    session.add(row)
    session.commit()

    if state == "BLOCKED" and not was_blocked:
        # Transition to BLOCKED (or first probe already BLOCKED):
        # owner notification with the artifact (D.1).
        enqueue_outbox(
            session=session,
            transport="TELEGRAM",
            recipient_hint="OWNER",
            subject=f"Канарейка {key}: канал {channel} ({_SCENARIO}) → BLOCKED",
            body=(
                f"Площадка {key}: ежедневная канарейка поиска BLOCKED.\n"
                f"Причина: {reason}\n"
                f"Артефакт: {artifact_path}\n"
                f"Публикации на канале не выполняются до восстановления."
            ),
            idempotency_key=f"canary-block-{key}-{_SCENARIO}-{datetime.now(UTC):%Y%m%d%H%M%S}",
        )
        session.commit()

    return CanaryResult(
        key=key,
        scenario=_SCENARIO,
        channel=channel,
        state=state,
        reason=reason,
        artifact_path=str(artifact_path),
        items_count=len(items),
        final_url=final_url,
    )


def _get_row(session: Session, key: str) -> ChannelCanary | None:
    return session.execute(
        select(ChannelCanary).where(
            ChannelCanary.marketplace_key == key,
            ChannelCanary.scenario == _SCENARIO,
        )
    ).scalar_one_or_none()


def is_direct_channel_blocked(session: Session, key: str) -> str | None:
    """D.1: publishing on a blocked channel is not executed.

    Returns the block reason if the DIRECT channel of the marketplace is
    BLOCKED by the canary, otherwise None.
    """
    row = session.execute(
        select(ChannelCanary).where(
            ChannelCanary.marketplace_key == key,
            ChannelCanary.channel == _DIRECT,
            ChannelCanary.state == "BLOCKED",
        )
    ).scalars().first()
    return row.block_reason if row is not None else None
