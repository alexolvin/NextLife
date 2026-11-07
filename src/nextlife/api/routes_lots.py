"""API routes for lot creation, wizard, and management (TZ v2.0 Section V.2, VI.1).

DoD:
- Unified lot creation endpoint using nextlife.services.lot_creation.
- Five-step wizard with persistent state saved at each step.
- Leaving at step 3 and returning preserves all entered state.
- Module length <= 400 lines.
"""

import json
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from nextlife.api.routes import check_auth, logical_photo_entries, templates
from nextlife.core.config import load_config
from nextlife.core.shipping_policy import normalize_shipping_policy, shipping_policy_options
from nextlife.obs.audit import record_audit_event
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing

lots_router = APIRouter(tags=["lots"])

# Дедлайн SSE-поиска мастера: висящая площадка (vinted без телефона) не
# блокирует 'done' дольше этой границы, а сообщается как timeout. 15 минут —
# безопасный потолок: Vinted-полный поиск (~9 мин, 20 карточек по share-flow)
# укладывается, а висящая площадка не держит стрим вечно. Пользователь может
# остановить поиск раньше кнопкой «Прервать» (частичные результаты сохраняются).
WIZARD_SEARCH_TIMEOUT_S = 900.0


def _validated_shipping_policy(shipping_policy: str | None) -> str | None:
    """TZ-NextLife-UI-N2 §4 V4 (P-E10): политика — только из единого словаря.

    None/пусто — поле не передано; устаревшее INCLUDED нормализуется к
    SELLER_PAYS; неизвестное значение — 400.
    """
    if shipping_policy is None or not str(shipping_policy).strip():
        return None
    policy = normalize_shipping_policy(shipping_policy)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown shipping policy {shipping_policy!r}; "
                f"available: {', '.join(p['value'] for p in shipping_policy_options())}"
            ),
        )
    return policy


class CreateLotPayload(BaseModel):
    title: str
    category: str
    price: float | None = None
    currency: str | None = None
    condition: str = "used"
    price_floor: float | None = None
    shipping_policy: str = "BUYER_PAYS"
    notes: str | None = None
    attrs: dict[str, Any] | None = None


@lots_router.post("/api/lots", status_code=status.HTTP_201_CREATED)
@lots_router.post("/api/items", status_code=status.HTTP_201_CREATED)
def api_create_lot(
    payload: CreateLotPayload,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Create a physical item and its lot commercial offer via JSON API."""
    price_dec = Decimal(str(payload.price)) if payload.price is not None else None
    floor_dec = Decimal(str(payload.price_floor)) if payload.price_floor is not None else None
    policy = _validated_shipping_policy(payload.shipping_policy) or "BUYER_PAYS"

    try:
        with get_db_session() as session:
            result = create_item_and_lot(
                session=session,
                title=payload.title,
                category_key=payload.category,
                price=price_dec,
                currency=payload.currency,
                condition_key=payload.condition,
                price_floor=floor_dec,
                shipping_policy=policy,
                notes=payload.notes,
                attrs=payload.attrs,
            )
            session.commit()
            return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@lots_router.get("/ui/lot-wizard", response_class=RedirectResponse)
def screen_lot_wizard_index() -> RedirectResponse:
    """ТЗ §3.4: /ui/lot-wizard -> /ui/stock (301)."""
    return RedirectResponse(url="/ui/stock", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@lots_router.get("/ui/create", response_class=RedirectResponse)
def screen_create_section() -> RedirectResponse:
    """ТЗ §3.4: /ui/create -> /ui/stock/new (301)."""
    return RedirectResponse(url="/ui/stock/new", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@lots_router.get("/ui/lot-wizard/item", response_class=RedirectResponse)
def screen_lot_wizard_item(request: Request, auth: bool = Depends(check_auth)) -> RedirectResponse:
    """ТЗ §3.4: /ui/lot-wizard/item -> /ui/stock/new (301)."""
    return RedirectResponse(url="/ui/stock/new", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@lots_router.post("/ui/lot-wizard/item")
def api_lot_wizard_item_new(
    title: str = Form(...),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Create a new Склад item from the step-1 «Новый предмет» form.

    Delegates to ``create_stock_item`` (ТЗ «Склад» §4.1); redirects to step 2
    with the new item's code.
    """
    from nextlife.services.stock import StockValidationError, create_stock_item

    try:
        with get_db_session() as session:
            created = create_stock_item(session, {"title": title})
            session.commit()
            code = created["code"]
    except StockValidationError:
        return RedirectResponse(
            url="/ui/lot-wizard/item?notice=empty_title",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/ui/lot-wizard/text?item_code={code}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@lots_router.get("/ui/lot-wizard/search", response_class=RedirectResponse)
def screen_lot_wizard_search() -> RedirectResponse:
    """Former step 1 (search) — now the «Аналоги» panel in step 2.

    Redirects to step 1 (Склад item). The search itself is still available as
    ``POST /ui/lot-wizard/search`` (SSE) from the step-2 panel.
    """
    return RedirectResponse(url="/ui/lot-wizard/item", status_code=status.HTTP_303_SEE_OTHER)


def _sse_event(event: str, data: str) -> str:
    """Format one SSE event; multi-line data becomes multiple data: lines."""
    parts = [f"event: {event}"]
    parts.extend(f"data: {line}" for line in data.split("\n"))
    parts.append("")
    parts.append("")
    return "\n".join(parts)


@lots_router.post("/ui/lot-wizard/search")
def api_lot_wizard_search(
    request: Request,
    query: str = Form(...),
    lang: str = Form("ru"),
    auth: bool = Depends(check_auth),
) -> Any:
    """Lot-creation wizard step 1: run the search and render results (draft).

    Vinted via the phone broker (ANDROID), Bazos/Aukro via web connectors.
    Aukro is filtered to «Kup teď» buy-now lots. Per-marketplace failures are
    shown honestly (no fabricated results).

    With ``Accept: text/event-stream`` the search is streamed as SSE
    (start → per-marketplace progress → done with the full results page),
    which lets the client show per-marketplace progress and cancel.
    Without it (non-JS fallback) the full page is returned as before.
    """
    from nextlife.connectors.registry import CONNECTOR_MAP, get_connector
    from nextlife.services.llm_filter import load_settings
    from nextlife.services.wizard_search import (
        filter_by_query,
        normalize_listing,
        search_across_marketplaces,
    )

    cfg = load_config()
    settings = load_settings()
    overrides = settings.get("marketplace_overrides", {})
    # Only search marketplaces that (a) have a connector implementation and
    # (b) have search enabled (config default + settings override). All 11
    # configured marketplaces now have a connector in CONNECTOR_MAP
    # (TZ-connectors-add-4, 2026-10-22): the four formerly-Android-only ones
    # (olx_pl, kleinanzeigen_de, willhaben_at, facebook_marketplace) gained
    # web search connectors; facebook_marketplace is an honest login-wall
    # placeholder (search() raises a classified error, reported per-market).
    # Toggled-off marketplaces are skipped per the settings switch.
    marketplaces = []
    for key in cfg.marketplaces.keys():
        if key not in CONNECTOR_MAP:
            continue
        ov = overrides.get(key, {})
        caps = cfg.marketplaces[key].capabilities
        caps = caps if isinstance(caps, dict) else {}
        if ov.get("search", caps.get("search", False)):
            marketplaces.append(key)

    # Перевод поискового ключа на язык площадки (раздел «язык» в шаге 1):
    # для площадок с другим языком ключ переводится LLM; если LLM недоступна
    # — ключ используется напрямую. Перевод кэшируется на язык (не на площадку).
    from nextlife.services.wizard_localize import country_lang_code, translate_query

    llm_config = settings.get("llm", {})
    translated: dict[str, str] = {}
    _trans_cache: dict[str, str] = {}
    for mp in marketplaces:
        target_lang = country_lang_code(cfg.marketplaces[mp])
        if target_lang == lang:
            translated[mp] = query
        elif target_lang not in _trans_cache:
            _trans_cache[target_lang] = translate_query(query, lang, target_lang, llm_config)
            translated[mp] = _trans_cache[target_lang]
        else:
            translated[mp] = _trans_cache[target_lang]

    def search_fn(q: str, mp_key: str) -> list[Any]:
        # Переведённый ключ (на язык площадки) вместо исходного, если есть.
        q = translated.get(mp_key, q)
        if mp_key == "vinted":
            try:
                connector = get_connector(mp_key, cfg)
                items = connector.search(q)
                if items:
                    return items
            except Exception:
                pass
            from nextlife.api.routes_openclaw import _search_vinted_android
            return _search_vinted_android(q, cfg, 20)
        connector = get_connector(mp_key, cfg)
        return connector.search(q)

    def render_page(results: list[dict[str, Any]], errors: dict[str, str]) -> Any:
        from fastapi.responses import JSONResponse
        return JSONResponse(content={"query": query, "results": results, "errors": errors})

    if "text/event-stream" not in request.headers.get("accept", ""):
        outcome = search_across_marketplaces(query, None, search_fn, cfg, 5)
        return render_page(outcome["results"], outcome["errors"])

    async def generator() -> Any:
        import asyncio
        import queue
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor

        from nextlife.services.llm_filter import get_filter_options

        # Дедлайн на весь поиск (потолок 900 с): висящая площадка не должна
        # блокировать 'done' вечно — она честно сообщается как timeout.
        search_timeout_s = WIZARD_SEARCH_TIMEOUT_S

        total = len(marketplaces)
        yield _sse_event("start", json.dumps({"marketplaces": marketplaces, "total": total}))
        results: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        completed: set[str] = set()
        filter_sent = False
        non_vinted = [mp for mp in marketplaces if mp != "vinted"]

        def _render_cards(entries: list[dict[str, Any]]) -> str:
            return templates.get_template("_wz_result_cards.html").render(results=entries)

        # «Прервать»: клиентский disconnect -> CancelledError в finally ->
        # cancel_event.set(); висящий Vinted share-flow останавливается на
        # следующей карточке и отзывает телефон (lease) вместо ~9 минут.
        cancel_event = threading.Event()
        # Прогресс Vinted по карточкам (share-flow идёт минуты): воркер
        # кладёт счётчик в очередь, генератор стримит его событиями 'progress'.
        progress_q: queue.Queue[int] = queue.Queue()

        def _search_one(mp: str) -> tuple[str, list[Any]]:
            q = translated.get(mp, query)
            if mp == "vinted":
                try:
                    connector = get_connector(mp, cfg)
                    items = connector.search(q)
                    if items:
                        return mp, items
                except Exception:
                    pass
                from nextlife.api.routes_openclaw import _search_vinted_android

                return mp, _search_vinted_android(q, cfg, 20, cancel_event, lambda n: progress_q.put(n))
            return mp, search_fn(q, mp)

        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=max(1, len(marketplaces)))
        try:
            futures = {loop.run_in_executor(pool, _search_one, mp): (mp, i) for i, mp in enumerate(marketplaces)}
            pending = set(futures)
            deadline = time.monotonic() + search_timeout_s
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    for future in pending:
                        mp, i = futures[future]
                        errors[mp] = f"Timeout: поиск не завершился за {int(search_timeout_s)} с"
                        yield _sse_event("error", json.dumps({"marketplace": mp, "error": errors[mp], "index": i, "total": total}, ensure_ascii=False))
                    break
                # Тик 2 с: генератор живёт, даже когда все площадки ещё в
                # работе (Vinted share-flow идёт минуты) — прогресс карточек
                # стримится событиями 'progress'.
                done_set, pending = await asyncio.wait(pending, timeout=min(remaining, 2.0), return_when=asyncio.FIRST_COMPLETED)
                while True:
                    try:
                        cards = progress_q.get_nowait()
                    except queue.Empty:
                        break
                    yield _sse_event("progress", json.dumps({"marketplace": "vinted", "cards": cards}, ensure_ascii=False))
                for future in done_set:
                    mp, i = futures[future]
                    completed.add(mp)
                    try:
                        _, raw_listings = future.result()
                        # Релевантность (владелец 2026-10-19): без искомой
                        # строки в заголовке/тексте — отсеять до фото. Ключ —
                        # переведённый (на язык площадки), т.к. заголовки на нём.
                        raw_listings = filter_by_query(raw_listings, translated.get(mp, query))
                        limit = 20 if mp == "vinted" else 5
                        raw_entries: list[dict[str, Any] | None] = [normalize_listing(raw, mp) for raw in raw_listings[:limit]]
                        entries: list[dict[str, Any]] = [e for e in raw_entries if e is not None]
                        results.extend(entries)
                        # Стримим карточки завершившейся площадки (клиент
                        # дописывает их в список; «Прервать» сохраняет накопленное).
                        yield _sse_event("result", json.dumps({
                            "marketplace": mp,
                            "count": len(entries),
                            "html": _render_cards(entries),
                            "index": i,
                            "total": total,
                        }, ensure_ascii=False))
                    except Exception as exc:
                        errors[mp] = f"{type(exc).__name__}: {exc}"
                        yield _sse_event("error", json.dumps({"marketplace": mp, "error": errors[mp], "index": i, "total": total}, ensure_ascii=False))
                    # LLM-фильтр считаем после всех быстрых (не-Vinted)
                    # площадок, не дожидаясь Vinted (~9 мин).
                    if not filter_sent and results and all(m in completed for m in non_vinted):
                        filter_data = get_filter_options(query, results)
                        if filter_data:
                            yield _sse_event("filter", json.dumps(filter_data, ensure_ascii=False))
                        filter_sent = True
        finally:
            # Клиент прервал стрим (или дедлайн): cancel_event останавливает
            # висящий Vinted share-flow (телефон освобождается), висящие
            # фаном-поиски не ждём при выходе.
            cancel_event.set()
            pool.shutdown(wait=False, cancel_futures=True)
        # Фильтр, если не был отправлен (например, только Vinted или все
        # быстрые площадки ошиблись, а Vinted отдал результаты).
        if not filter_sent and results:
            filter_data = get_filter_options(query, results)
            if filter_data:
                yield _sse_event("filter", json.dumps(filter_data, ensure_ascii=False))
        # Ошибки уже отправлены событиями 'error'; 'done' — только сигнал конца.
        yield _sse_event("done", json.dumps({}))

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@lots_router.get("/ui/lot-wizard/text", response_class=RedirectResponse)
def screen_lot_wizard_text(
    request: Request,
    item_code: str = Query(""),
    notice: str | None = Query(None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ §3.4: /ui/lot-wizard/text?item_code=X -> /ui/stock/X#describe (301)."""
    if item_code:
        return RedirectResponse(
            url=f"/ui/stock/{item_code}#describe",
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
        )
    return RedirectResponse(url="/ui/stock/new", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@lots_router.post("/ui/lot-wizard/text", response_class=HTMLResponse)
def api_lot_wizard_text(
    request: Request,
    item_code: str = Form(""),
    title: str = Form(""),
    notes: str = Form(""),
    auth: bool = Depends(check_auth),
) -> Any:
    """Lot-creation wizard step 2: «Сгенерировать описание».

    Calls ``generate_description`` (language = ``item.description_lang``) and
    writes the result to ``item.title`` + ``item.description`` via
    ``update_stock_item`` (ТЗ «Склад» §4.2.2) — not to
    ``attrs["wizard_generated_text"]``. When the item already has a
    non-empty description the client confirms «Заменить текущее описание?»
    before submitting.
    """
    from nextlife.services.stock import update_stock_item
    from nextlife.services.wizard_textgen import generate_description

    notice = "generate_no_llm"
    with get_db_session() as session:
        item = session.get(Item, item_code) if item_code else None
        if item:
            lang = item.description_lang or "ru"
            gen = generate_description(
                item_code, None, item.notes or "", session=session, lang=lang
            )
            if gen.get("mode") == "live":
                fields: dict[str, Any] = {}
                if gen.get("title"):
                    fields["title"] = gen["title"]
                if gen.get("text"):
                    fields["description"] = gen["text"]
                if fields:
                    update_stock_item(session, item_code, fields)
                    session.commit()
                    notice = "generated"

    return RedirectResponse(
        url=f"/ui/lot-wizard/text?item_code={item_code}&notice={notice}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@lots_router.post("/ui/lot-wizard/draft", response_class=HTMLResponse)
def api_lot_wizard_draft(
    request: Request,
    selected: list[str] = Form([]),
    item_code: str = Form(""),
    notes: str = Form(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Lot-creation wizard step 2: create/supplement the Item draft (draft).

    Each checked result is one ``selected`` form value carrying the normalized
    listing as a JSON string (plain URLs are accepted too). The default
    category applies (category mapping is a later, per-marketplace task).
    """
    import json

    from nextlife.services.wizard_draft import create_draft_from_selection

    selected_list: list[dict[str, Any]] = []
    for value in selected:
        try:
            entry = json.loads(value)
            selected_list.append(entry if isinstance(entry, dict) else {"url": value})
        except json.JSONDecodeError:
            selected_list.append({"url": value})

    with get_db_session() as session:
        code = create_draft_from_selection(
            session, selected_list, notes, item_code or None, None
        )
        session.commit()

    return RedirectResponse(
        url=f"/ui/lot-wizard/text?item_code={code}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@lots_router.post("/ui/lot-wizard/{code}/photos/upload")
def api_lot_wizard_upload_photos(
    code: str,
    files: list[UploadFile] = File(...),
    role: str = Form("ORIGINAL"),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Lot-creation wizard step 2: upload photos (existing ItemPhoto mechanism)."""
    from nextlife.api.routes_photos import _process_uploads

    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")
        _process_uploads(session, code, files, role)
        session.commit()

    return RedirectResponse(
        url=f"/ui/lot-wizard/text?item_code={code}&notice=photos_uploaded",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@lots_router.get("/ui/lot-wizard/finalize", response_class=RedirectResponse)
def screen_lot_wizard_finalize(
    request: Request,
    item_code: str = Query(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ §3.4: /ui/lot-wizard/finalize?item_code=X -> /ui/stock/X#listings (301)."""
    if item_code:
        return RedirectResponse(
            url=f"/ui/stock/{item_code}#listings",
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
        )
    return RedirectResponse(url="/ui/stock", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@lots_router.post("/ui/lot-wizard/finalize/frame/generate")
def api_lot_wizard_frame_generate(
    request: Request,
    item_code: str = Form(...),
    mp_key: str = Form(...),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Step 3: generate + save the localized frame for one marketplace (LLM).

    Localizes the listing (translated title/description, category, default
    price/currency, main photo) and saves it as a localized frame; the page
    re-renders with the filled frame and the «Утвердить» button.
    """
    from nextlife.services.wizard_localize import localize_frame, save_frame

    cfg = load_config()
    with get_db_session() as session:
        photos = logical_photo_entries(session, item_code)
        frame = localize_frame(session, item_code, mp_key, photos, cfg)
        save_frame(session, item_code, mp_key, frame, approved=False)
        session.commit()

    return RedirectResponse(
        url=f"/ui/lot-wizard/finalize?item_code={item_code}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@lots_router.post("/ui/lot-wizard/finalize/frame/save")
def api_lot_wizard_frame_save(
    request: Request,
    item_code: str = Form(...),
    mp_key: str = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    price: str = Form(""),
    currency: str = Form(""),
    main_photo: str = Form(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Step 3: save the localized frame as approved (the frame goes to step 4)."""
    from nextlife.services.wizard_localize import (
        resolve_category_for_marketplace,
        save_frame,
    )

    cfg = load_config()
    mp_cfg = cfg.marketplaces.get(mp_key)
    saved_cat = category
    if mp_cfg:
        saved_cat = resolve_category_for_marketplace(category, mp_cfg)["canonical"]

    with get_db_session() as session:
        save_frame(
            session,
            item_code,
            mp_key,
            {
                "title": title,
                "description": description,
                "category": saved_cat,
                "price": price,
                "currency": currency,
                "main_photo": main_photo,
            },
            approved=True,
        )
        session.commit()

    return RedirectResponse(
        url=f"/ui/lot-wizard/finalize?item_code={item_code}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@lots_router.get("/ui/lot-wizard/publish", response_class=RedirectResponse)
def screen_lot_wizard_publish(
    request: Request,
    item_code: str = Query(""),
    notice: str = Query(""),
    err: str = Query(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ §3.4: /ui/lot-wizard/publish?item_code=X -> /ui/stock/X#listings (301)."""
    if item_code:
        return RedirectResponse(
            url=f"/ui/stock/{item_code}#listings",
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
        )
    return RedirectResponse(url="/ui/stock", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@lots_router.post("/ui/lot-wizard/publish/dry-run")
def api_lot_wizard_publish_dry_run(
    request: Request,
    item_code: str = Form(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Step 4: controlled dry-run publish check on the approved frames (no live publish)."""
    from nextlife.core.exceptions import NextLifeError
    from nextlife.services.wizard_publish_dry_run import publish_dry_run

    try:
        with get_db_session() as session:
            publish_dry_run(session, item_code)
            session.commit()
    except NextLifeError as exc:
        return RedirectResponse(
            url=f"/ui/lot-wizard/publish?item_code={item_code}&notice=dry_run_error&err={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/ui/lot-wizard/publish?item_code={item_code}&notice=dry_run_done",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@lots_router.post("/ui/lot-wizard/publish/bazos")
def api_lot_wizard_publish_bazos(
    request: Request,
    item_code: str = Form(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Step 4: publish the approved frame through the Bazoš Android app."""
    from nextlife.services.bazos_android_publish import bazos_android_publish_listing

    with get_db_session() as session:
        item = session.get(Item, item_code)
        if not item:
            return RedirectResponse(
                url=f"/ui/lot-wizard/publish?item_code={item_code}&notice=publish_error&err={quote('Лот не найден')}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        frames = (item.attrs or {}).get("wizard_frames") or {}
        frame = frames.get("bazos_cz") or {}

    if not frame or frame.get("status") != "approved":
        return RedirectResponse(
            url=f"/ui/lot-wizard/publish?item_code={item_code}&notice=publish_error&err={quote('Bazoš frame не утверждён')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    result = bazos_android_publish_listing(
        title=frame.get("title", ""),
        description=frame.get("description", ""),
        price=str(frame.get("price", "")),
        category=frame.get("category") or "",
        item_code=item_code,
    )

    # ТЗ «Склад» §2.2: audit для каждой попытки + listing при result.ok.
    with get_db_session() as session:
        record_audit_event(
            session=session,
            actor="OWNER",
            action="LOT_PUBLISH_ATTEMPT",
            entity="lot",
            entity_id=item_code,
            data={
                "marketplace": "bazos_cz",
                "ok": bool(result.get("ok")),
                "final_url": result.get("final_url"),
                "evidence_path": result.get("evidence_path"),
                "errors": result.get("errors", []),
            },
        )
        if result.get("ok"):
            from datetime import UTC, datetime

            price = Decimal(str(frame.get("price", "0")) or "0")
            currency = str(frame.get("currency") or "CZK")
            listing = Listing(
                item_code=item_code,
                marketplace_key="bazos_cz",
                price=price,
                currency=currency,
                price_base=price,
                status="PUBLISHED",
                published_at=datetime.now(UTC),
                external_url=result.get("final_url"),
                publish_evidence={
                    "url": result.get("final_url"),
                    "evidence_file": result.get("evidence_path"),
                    "timestamp": result.get("timestamp"),
                    "live_status": "LIVE_VERIFIED",
                },
            )
            session.add(listing)
            session.commit()
            notice = "publish_done"
            detail = result.get("steps", [])[-1] if result.get("steps") else ""
        else:
            session.commit()
            notice = "publish_error"
            detail = result.get("errors", ["unknown error"])[0]

    return RedirectResponse(
        url=f"/ui/lot-wizard/publish?item_code={item_code}&notice={notice}&err={quote(detail)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )

