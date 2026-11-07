"""OpenClaw-skill HTTP API (item 3): items, research, draft, publish.

Backend for the thin NextLife CLI: every endpoint is authenticated ONLY with
NEXTLIFE_OPENCLAW_TOKEN (Bearer header, no cookie, no ?token=, no web-token
fallback). This namespace never opens /ui or the web /api endpoints; the web
token never opens these endpoints (different SecretRef).

Rule (owner, 2026-10-04): an endpoint that changes a published listing
requires a current draft approval recorded via the web UI; otherwise 409.

Module length rule VI.1.1 (<= 400 lines). Operations (media/comms/canaries)
live in routes_openclaw_ops.py (same /api/oc namespace).
"""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from nextlife.core.approval import has_valid_approval
from nextlife.core.config import get_openclaw_token, load_config
from nextlife.core.exceptions import InputError, NextLifeError
from nextlife.core.status import ItemStatus
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto, Job, Listing, Marketplace, ResearchRun

oc_router = APIRouter(prefix="/api/oc", tags=["openclaw"])


def check_openclaw_auth(authorization: str | None = Header(default=None)) -> bool:
    """Verify the OpenClaw skill token (NEXTLIFE_OPENCLAW_TOKEN only)."""
    expected = get_openclaw_token()
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    if not token or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing openclaw token.",
        )
    return True


_AUTH = Depends(check_openclaw_auth)


def require_current_approval(session: Any, item_code: str) -> None:
    """409 if the CURRENT draft has no approval or the approved hash differs."""
    from nextlife.services.sell import build_draft

    draft = build_draft(session, item_code)
    if not has_valid_approval(session, item_code, draft["payload_hash"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Draft for item {item_code} is not approved or was modified after "
                f"approval. Approve the current draft in the web UI: /ui/draft/{item_code}"
            ),
        )


class OcItemCreate(BaseModel):
    title: str
    category: str
    price: float | None = None
    currency: str | None = None
    condition: str = "used"


class OcPublishBody(BaseModel):
    dry_run: bool = False


@oc_router.get("/status")
def oc_status(auth: bool = _AUTH) -> dict[str, Any]:
    cfg = load_config()
    items_by_status: dict[str, int] = {}
    jobs_count = 0
    connectors: dict[str, bool] = {}
    with get_db_session() as session:
        for item in session.query(Item).all():
            items_by_status[item.status] = items_by_status.get(item.status, 0) + 1
        jobs_count = session.query(Job).count()
        for mp in session.query(Marketplace).all():
            connectors[mp.key] = mp.enabled
    if not connectors:
        connectors = dict.fromkeys(cfg.marketplaces, False)
    return {
        "items_by_status": items_by_status,
        "jobs_in_queue": jobs_count,
        "recent_errors": [],
        "connectors": connectors,
        "last_fx_refresh": None,
        "android_bridge": {
            "enabled": cfg.app.android_bridge.enabled,
            "status": "disabled" if not cfg.app.android_bridge.enabled else "standby",
        },
    }


@oc_router.post("/items", status_code=status.HTTP_201_CREATED)
def oc_item_create(payload: OcItemCreate, auth: bool = _AUTH) -> dict[str, Any]:
    """ТЗ «Склад» §5.2.1: create via the stock service (same record as POST /api/oc/stock)."""
    from nextlife.services.stock import StockValidationError, create_stock_item

    fields: dict[str, Any] = {
        "title": payload.title,
        "category_key": payload.category,
        "price": payload.price,
        "currency": payload.currency,
        "condition_key": payload.condition,
    }
    with get_db_session() as session:
        try:
            result = create_stock_item(session, fields)
            session.commit()
            return result
        except StockValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except (InputError, NextLifeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@oc_router.get("/items/{code}")
def oc_item_show(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    """ТЗ «Склад» §5.2.2: the same card as GET /api/oc/stock/{code}."""
    from nextlife.services.stock import StockItemNotFoundError, get_stock_item

    with get_db_session() as session:
        try:
            return get_stock_item(session, code)
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}") from exc


@oc_router.post("/items/{code}/research")
def oc_research_start(code: str, query: str | None = None, auth: bool = _AUTH) -> dict[str, Any]:
    from nextlife.services.research import run_research

    cfg = load_config()
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}")
        try:
            run_id = run_research(session, code, query or item.title, cfg)
            session.commit()
            return {"status": "completed", "item_code": code, "run_id": run_id}
        except NextLifeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@oc_router.get("/items/{code}/research/report")
def oc_research_report(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    from nextlife.services.research import build_research_report

    cfg = load_config()
    with get_db_session() as session:
        latest = (
            session.execute(
                select(ResearchRun).where(ResearchRun.item_code == code).order_by(ResearchRun.started_at.desc())
            )
            .scalars()
            .first()
        )
        if not latest:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No research runs found for item {code}")
        report = build_research_report(session, latest.id, cfg)
        return {"status": "ok", "item_code": code, "report": report}


@oc_router.post("/items/{code}/draft")
def oc_draft_build(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    from nextlife.services.sell import build_draft

    cfg = load_config()
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}")
        draft = build_draft(session, code, cfg)
        if item.status == ItemStatus.DRAFT.value:
            item.status = ItemStatus.AWAITING_APPROVAL.value
        session.commit()
        return {
            "status": "draft_ready",
            "item_code": code,
            "payload_hash": draft["payload_hash"],
            "draft": draft,
            "web_url": f"/ui/draft/{code}",
        }


@oc_router.post("/items/{code}/publish")
def oc_publish(code: str, body: OcPublishBody, auth: bool = _AUTH) -> dict[str, Any]:
    from nextlife.services.sell import MissingApprovalError, build_draft, publish_item

    cfg = load_config()
    with get_db_session() as session:
        if not session.get(Item, code):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}")
        require_current_approval(session, code)
        draft = build_draft(session, code, cfg)
        try:
            result = publish_item(session, code, draft, config=cfg, dry_run=body.dry_run)
            session.commit()
            return {"status": "published", "item_code": code, "results": result}
        except MissingApprovalError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NextLifeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@oc_router.get("/items/{code}/listings")
def oc_publish_status(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        if not session.get(Item, code):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}")
        listings = [
            {"marketplace": lst.marketplace_key, "status": lst.status, "url": lst.external_url, "price": str(lst.price)}
            for lst in session.query(Listing).filter(Listing.item_code == code).all()
        ]
        return {"item_code": code, "listings": listings}


class OcSearchBody(BaseModel):
    platform: str
    query: str
    limit: int = 5


@oc_router.post("/search")
def oc_search(body: OcSearchBody, auth: bool = _AUTH) -> dict[str, Any]:
    """Search a marketplace app via the phone broker (Stage 2/3)."""
    from nextlife.android.bridge import AndroidBridge
    from nextlife.android.errors import AndroidBridgeError
    from nextlife.android.results import parse_results
    from nextlife.android.transport import make_phone_broker_transport

    cfg = load_config()
    mp_cfg = cfg.marketplaces.get(body.platform)
    if mp_cfg is None or mp_cfg.android is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Marketplace '{body.platform}' has no ANDROID channel configured.",
        )
    android_cfg = cfg.app.android_bridge
    if not android_cfg.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ANDROID channel is disabled in config.",
        )
    try:
        transport = make_phone_broker_transport(android_cfg.phone_broker)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cannot create broker transport: {exc}") from exc

    marketplaces = {
        key: m.android
        for key, m in cfg.marketplaces.items()
        if m.android is not None
    }
    with get_db_session() as session:
        bridge = AndroidBridge(
            android_cfg,
            transport,
            session=session,
            marketplaces=marketplaces,
        )
        try:
            result = bridge.search(body.platform, body.query)
        except AndroidBridgeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    xml = result.data.get("results_xml", "")
    links = result.data.get("links", [])
    card_selector = mp_cfg.android.search.result_card
    results = parse_results(xml, card_selector, limit=body.limit)
    return {
        "platform": body.platform,
        "query": body.query,
        "app_version": result.evidence.app_version,
        "screen": result.data.get("screen", ""),
        "count": len(results),
        "results": [
            {"title": r.title, "price": r.price, "currency": r.currency,
             "link": links[i] if i < len(links) else ""}
            for i, r in enumerate(results)
        ],
    }


class OcWizardSearchBody(BaseModel):
    query: str
    marketplaces: list[str] | None = None
    limit: int = 5


def _search_vinted_android(
    query: str,
    cfg: Any,
    limit: int,
    cancel_event: Any = None,
    on_progress: Any = None,
) -> list[Any]:
    """Vinted search via the phone broker (ANDROID channel).

    Returns a list of RawListing. Raises CapabilityNotSupported when the phone
    channel is unavailable (honest NOT-STARTED/BLOCKED), never fabricates.
    ``cancel_event`` (threading.Event): when set (wizard «Прервать»), the
    share flow stops at the next card and returns the links collected so far.
    ``on_progress`` (callable(int)): called with the number of links
    collected so far after each card (wizard SSE progress line).
    """
    import logging
    import time
    from decimal import Decimal

    from nextlife.android.bridge import AndroidBridge, AndroidBridgeError
    from nextlife.android.results import parse_results
    from nextlife.android.transport import make_phone_broker_transport
    from nextlife.connectors.base import CapabilityNotSupported, RawListing
    from nextlife.connectors.og_image import fetch_og_image

    log = logging.getLogger("nextlife.vinted_search")
    t0 = time.monotonic()

    mp_cfg = cfg.marketplaces["vinted"]
    android_cfg = cfg.app.android_bridge
    if mp_cfg.android is None:
        raise CapabilityNotSupported("vinted: no ANDROID channel configured.")
    if not android_cfg.enabled:
        raise CapabilityNotSupported("vinted: ANDROID channel disabled in config.")
    transport = make_phone_broker_transport(android_cfg.phone_broker)
    marketplaces = {key: m.android for key, m in cfg.marketplaces.items() if m.android is not None}
    with get_db_session() as session:
        bridge = AndroidBridge(android_cfg, transport, session=session, marketplaces=marketplaces)
        try:
            log.info("vinted search: bridge.search start (elapsed %.1fs)", time.monotonic() - t0)
            result = bridge.search("vinted", query, link_limit=limit, cancel_event=cancel_event, on_progress=on_progress)
            log.info("vinted search: bridge.search done (elapsed %.1fs)", time.monotonic() - t0)
        except AndroidBridgeError as exc:
            log.error("vinted search: bridge.search failed (elapsed %.1fs): %s", time.monotonic() - t0, exc)
            raise CapabilityNotSupported(f"vinted: phone broker search failed: {exc}") from exc
    xml = result.data.get("results_xml", "")
    links = result.data.get("links", [])
    card_selector = mp_cfg.android.search.result_card
    parsed = parse_results(xml, card_selector, limit=limit)
    raw_listings: list[RawListing] = []
    for i, r in enumerate(parsed):
        link = links[i] if i < len(links) else ""
        try:
            price = Decimal(r.price) if r.price else Decimal("0")
        except Exception:
            price = Decimal("0")
        raw_listings.append(
            RawListing(
                title=r.title,
                price=price,
                currency=r.currency or mp_cfg.currency,
                external_url=link,
                condition_text=None,
                posted_at=None,
                raw={"source": "android_phone_broker", "desc": r.card_desc},
            )
        )
    # Relevance (owner 2026-10-19): drop cards that do not contain the query
    # string in title/text BEFORE the og:image fetch — no photo requests for
    # irrelevant listings.
    from nextlife.services.wizard_search import listing_matches_query

    raw_listings = [
        listing for listing in raw_listings
        if listing_matches_query(listing.title, query, str(listing.raw.get("desc", "")))
    ]
    # Thumbnails: the Android search dump has no card images, so fetch each
    # listing page's og:image (parallel, best-effort; a blocked page yields
    # "" and the wizard card shows its placeholder).
    if raw_listings:
        from concurrent.futures import ThreadPoolExecutor
        from dataclasses import replace

        with ThreadPoolExecutor(max_workers=5) as img_pool:
            images = list(img_pool.map(fetch_og_image, [listing.external_url for listing in raw_listings]))
        raw_listings = [
            replace(listing, raw={**listing.raw, "image_url": img})
            for listing, img in zip(raw_listings, images, strict=True)
        ]
    log.info("vinted search: complete, %d listings (total %.1fs)", len(raw_listings), time.monotonic() - t0)
    return raw_listings


@oc_router.post("/wizard/search")
def oc_wizard_search(body: OcWizardSearchBody, auth: bool = _AUTH) -> dict[str, Any]:
    """Wizard step 1: search across marketplaces. Vinted via the phone broker
    (ANDROID), Bazos/Aukro via their web connectors. Aukro is filtered to
    «Kup teď» buy-now lots. Per-marketplace failures are reported honestly."""
    from nextlife.connectors.registry import get_connector
    from nextlife.services.wizard_search import search_across_marketplaces

    cfg = load_config()

    def search_fn(query: str, mp_key: str) -> list[Any]:
        if mp_key == "vinted":
            try:
                connector = get_connector(mp_key, cfg)
                items = connector.search(query, limit=body.limit)
                if items:
                    return items
            except Exception:
                pass
            return _search_vinted_android(query, cfg, body.limit)
        connector = get_connector(mp_key, cfg)
        return connector.search(query)

    return search_across_marketplaces(body.query, body.marketplaces, search_fn, cfg, body.limit)


class OcWizardTextBody(BaseModel):
    item_code: str
    selected: list[dict[str, Any]] | None = None
    notes: str = ""


@oc_router.post("/wizard/text")
def oc_wizard_text(body: OcWizardTextBody, auth: bool = _AUTH) -> dict[str, Any]:
    """Wizard step 2: generate a listing description and write it to the item.

    ТЗ «Склад» §5.2.3: the result is written to ``item.title`` +
    ``item.description`` via ``update_stock_item`` (rules 4.2); language =
    ``item.description_lang``.
    """
    from nextlife.services.stock import update_stock_item
    from nextlife.services.wizard_textgen import generate_description

    with get_db_session() as session:
        item = session.get(Item, body.item_code)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {body.item_code}")
        lang = item.description_lang or "ru"
        gen = generate_description(body.item_code, body.selected, body.notes, session=session, lang=lang)
        if gen.get("mode") == "live":
            fields: dict[str, Any] = {}
            if gen.get("title"):
                fields["title"] = gen["title"]
            if gen.get("text"):
                fields["description"] = gen["text"]
            if fields:
                update_stock_item(session, body.item_code, fields)
                session.commit()
        return gen


class OcWizardDraftBody(BaseModel):
    selected: list[dict[str, Any]] = []
    item_code: str | None = None
    notes: str = ""
    category: str | None = None


@oc_router.post("/wizard/draft")
def oc_wizard_draft(body: OcWizardDraftBody, auth: bool = _AUTH) -> dict[str, Any]:
    """Wizard step 2: create/supplement an Item draft from selected results.

    If ``item_code`` is given, the existing Item is supplemented; otherwise a
    new Item (kind=SELL, 3-char code) is created. Selected results are stored
    in ``attrs["wizard_selected"]``. ``category`` (a declared category key)
    is used for a new Item; without it the default category applies.
    Returns the item_code.
    """
    from nextlife.services.wizard_draft import create_draft_from_selection

    with get_db_session() as session:
        item_code = create_draft_from_selection(
            session, body.selected, body.notes, body.item_code, body.category
        )
        session.commit()
    return {"item_code": item_code}


class OcWizardFinalizeBody(BaseModel):
    item_code: str
    price: float | None = None
    currency: str | None = None
    marketplaces: list[str] = []
    publish_at: str | None = None
    repost: bool = False


@oc_router.post("/wizard/finalize")
def oc_wizard_finalize(body: OcWizardFinalizeBody, auth: bool = _AUTH) -> dict[str, Any]:
    """Wizard step 3: save the final publish parameters (approval, no publish).

    Stores price/currency in the existing price fields (Item.target_*,
    Lot.price_gross/currency) and marketplaces/publish_at/repost in
    ``attrs["wizard_*"]``; sets ``attrs["wizard_status"] = "params_approved"``.
    No publish is performed.
    """
    from nextlife.services.wizard_finalize import finalize_draft_params

    price = Decimal(str(body.price)) if body.price is not None else None
    with get_db_session() as session:
        code = finalize_draft_params(
            session,
            body.item_code,
            price=price,
            currency=body.currency,
            marketplaces=body.marketplaces,
            publish_at=body.publish_at,
            repost=body.repost,
        )
        session.commit()
    return {"item_code": code, "wizard_status": "params_approved"}


class OcWizardDryRunBody(BaseModel):
    item_code: str


@oc_router.post("/wizard/publish-dry-run")
def oc_wizard_publish_dry_run(body: OcWizardDryRunBody, auth: bool = _AUTH) -> dict[str, Any]:
    """Wizard step 3: controlled dry-run publish check for the selected
    marketplaces. Never performs a live publication; the report is stored in
    ``attrs["wizard_dry_run_report"]``."""
    from nextlife.services.wizard_publish_dry_run import publish_dry_run

    with get_db_session() as session:
        report = publish_dry_run(session, body.item_code)
        session.commit()
    return {"item_code": body.item_code, "report": report}
