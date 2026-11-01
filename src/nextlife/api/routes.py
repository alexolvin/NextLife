"""FastAPI routes for NextLife Re-Commerce Web-UI (TZ v2.0 Section V.1, E7)."""

from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nextlife.core.approval import has_valid_approval, record_approval
from nextlife.core.config import get_media_dir, load_config
from nextlife.core.secrets import resolve_secret
from nextlife.core.status import ItemStatus, deal_status_label_ru, status_label_ru
from nextlife.media.jobs import sync_item_media
from nextlife.media.pipeline import display_photo_for_chain, logical_photo_chains
from nextlife.money.money import format_money
from nextlife.services.research import build_research_panel
from nextlife.services.sell import build_draft
from nextlife.services.stock import update_stock_item
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item

router = APIRouter()

from nextlife.version import get_version_str

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "web"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# TZ-NextLife-UI-N2 §1.4: единственная точка форматирования сумм в шаблонах.
templates.env.filters["money"] = format_money
# Cache-busting: версия CSS = mtime app.css; меняет URL (?v=...) при каждом
# изменении файла, чтобы клиенты (в т.ч. DuckDuckGo с кэшем по ETag) подхватили новые стили.
_APP_CSS = Path(__file__).resolve().parent / "static" / "app.css"
templates.env.globals["asset_ver"] = int(_APP_CSS.stat().st_mtime)
templates.env.globals["app_version"] = get_version_str()
# TZ-NextLife-UI-N2 §4 V4 (P-E15): статусы — только по-русски (Э-6).
templates.env.filters["status_ru"] = status_label_ru
templates.env.filters["deal_status_ru"] = deal_status_label_ru

def resolve_photo_url(path: str | None) -> str:
    """Resolve photo file path to a valid web URL under /media/."""
    if not path:
        return ""
    p = Path(path)
    if "media" in p.parts:
        idx = p.parts.index("media")
        rel = "/".join(p.parts[idx:])
        return f"/{rel}"
    return f"/media/{p.name}"


def media_url_exists(url: str) -> bool:
    """Whether a /media/... URL resolves to a real file on disk (P-E05)."""
    if not url.startswith("/media/"):
        return False
    # Корень медиа = get_media_dir(): тот же, что mount /media в main.py (P-E05).
    return (get_media_dir() / url[len("/media/"):]).exists()


def logical_photo_entries(session: Session, item_code: str) -> list[dict[str, Any]]:
    """TZ-NextLife-UI-N2 §4 V3 (Э-6): одно логическое фото на цепочку.

    Каждой цепочке — одна запись: публикуемая версия как показываемая
    (P-E04b), id корня (крестик удаляет всю цепочку) и пометка битого файла
    (P-E05) вместо мёртвой ссылки.
    """
    item = session.get(Item, item_code)
    cover_id = (item.attrs or {}).get("cover_photo_id") if item else None
    entries: list[dict[str, Any]] = []
    for chain in logical_photo_chains(session, item_code):
        photo = display_photo_for_chain(chain)
        url = resolve_photo_url(photo.path)
        broken = bool(url) and not media_url_exists(url)
        # TZ-NextLife-UI-N2 §4 V6: display_id — цель пер-фото операций;
        # подсказки по качеству — с корня цепочки (загруженного оригинала).
        hints = (chain[0].vlm_verdict or {}).get("hints") or []
        # Cache-busting: ?v=sha256[:8] — после любой пер-фото операции
        # (поворот/обрезка/пыль/фон) меняется sha256 → меняется URL → браузер
        # подтягивает свежий файл, а не кэш (иначе кнопки «ничего не меняют»).
        ver = f"?v={photo.sha256[:8]}" if photo.sha256 else ""
        entries.append({
            "id": chain[0].id,
            "display_id": photo.id,
            "role": photo.role,
            # битый файл: url пуст — шаблон/JS показывают плейсхолдер, 404 не летит
            "url": "" if broken else url + ver,
            "broken": broken,
            "ai": bool(photo.ai_generated),
            "is_cover": cover_id == photo.id,
            "hints": hints,
        })
    return entries


def get_status_badge_class(item_status: str) -> str:
    """Return styling badge class for item status."""
    if item_status == ItemStatus.AWAITING_APPROVAL.value:
        return "badge badge-warning"
    elif item_status == ItemStatus.APPROVED.value:
        return "badge badge-success"
    elif item_status in (ItemStatus.PUBLISHED.value, ItemStatus.PARTIALLY_PUBLISHED.value):
        return "badge badge-primary"
    return "badge"


def check_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    nextlife_token: str | None = Cookie(default=None),
) -> bool:
    """Verify security token for Web-UI access (single secret token)."""
    cfg = load_config()
    expected_token = resolve_secret(cfg.app.auth.web_token_secret_ref)

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif nextlife_token:
        token = nextlife_token.strip()
    elif request.query_params.get("token"):
        q_tok = request.query_params.get("token")
        token = q_tok.strip() if q_tok else None

    if not token or token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing security token.",
        )
    return True


@router.get("/", response_class=RedirectResponse)
@router.get("/ui", response_class=RedirectResponse)
def index_redirect(request: Request) -> RedirectResponse:
    q = str(request.url.query)
    return RedirectResponse(url=f"/ui/items{('?' + q) if q else ''}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/ui/login", response_class=HTMLResponse)
def screen_login(
    request: Request,
    error: str | None = None,
    next_url: str | None = Query(default="/ui/items", alias="next"),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "next_url": next_url, "active_tab": "login"},
    )


@router.post("/ui/login")
def process_login(
    response: Response,
    token: str = Form(...),
    next_url: str = Form(default="/ui/items"),
) -> RedirectResponse:
    cfg = load_config()
    expected_token = resolve_secret(cfg.app.auth.web_token_secret_ref)
    if token.strip() != expected_token:
        err_msg = "Неверный токен безопасности. Пожалуйста, проверьте ввод."
        return RedirectResponse(
            url=f"/ui/login?error={err_msg}&next={next_url}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    resp = RedirectResponse(url=next_url or "/ui/items", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(key="nextlife_token", value=token.strip(), httponly=True, samesite="lax")
    return resp


@router.post("/ui/sync")
@router.get("/ui/sync")
def api_sync_refresh() -> dict[str, Any]:
    return {"status": "ok", "synced": True}


@router.get("/ui/items", response_class=RedirectResponse)
def screen_items_feed_redirect(
    request: Request,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ «Склад» §3.1.3: /ui/items -> /ui/stock (301)."""
    q = str(request.url.query)
    return RedirectResponse(url=f"/ui/stock{('?' + q) if q else ''}", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/ui/items/{item_code}", response_class=RedirectResponse)
def screen_item_detail_redirect(
    request: Request,
    item_code: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ «Склад» §3.1.3: /ui/items/{code} -> /ui/stock/{code} (301)."""
    q = str(request.url.query)
    return RedirectResponse(
        url=f"/ui/stock/{item_code}{('?' + q) if q else ''}",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@router.get("/ui/draft/{item_code}", response_class=HTMLResponse)
def screen_draft_approval(
    request: Request,
    item_code: str,
    notice: str | None = None,
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    with get_db_session() as session:
        item = session.execute(select(Item).where(Item.code == item_code)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        item_data = {"code": item.code}
        draft = build_draft(session, item_code)
        # TZ-NextLife-UI-N2 §4 V3 (P-E04): полоса фото черновика — по
        # логическим фото (цепочкам), публикуемые версии, битые — помечены.
        sync_item_media(session, item_code)
        photos = logical_photo_entries(session, item_code)
        # TZ-NextLife-UI-N2 §4 V5 (P-R07): панель разведки — последний run.
        research = build_research_panel(session, item, load_config())

    cfg = load_config()
    char_limit = min((mp.max_description_length for mp in cfg.marketplaces.values()), default=3000)

    return templates.TemplateResponse(
        request=request,
        name="draft.html",
        context={
            "item": item_data,
            "draft": draft,
            "photos": photos,
            "char_limit": char_limit,
            "payload_hash": draft.get("payload_hash", ""),
            "active_tab": "items",
            "notice": notice,
            "research": research,
            "research_notice": (
                notice if notice in ("research_done", "research_failed") else None
            ),
            "research_back": f"/ui/draft/{item_code}",
        },
    )


@router.post("/ui/draft/{item_code}/approve")
def api_approve_draft(
    item_code: str,
    payload_hash: str = Form(default=""),
    params_payload_hash: str | None = Query(default=None, alias="payload_hash"),
    auth: bool = Depends(check_auth),
) -> Any:
    target_hash = params_payload_hash or payload_hash
    with get_db_session() as session:
        item = session.execute(select(Item).where(Item.code == item_code)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        draft = build_draft(session, item_code)
        canonical_hash = draft["payload_hash"]

        # TZ-NextLife-UI-N2 §3.3 P-E03: серверный блок — утверждение
        # невозможно, пока русский текст не заменён на чешский.
        if draft.get("language_warning"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=draft["language_warning"],
            )

        if target_hash and target_hash != canonical_hash:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payload hash mismatch! Current draft hash: {canonical_hash}",
            )

        if not has_valid_approval(session, item_code, canonical_hash):
            payload_to_store = {k: v for k, v in draft.items() if k != "payload_hash"}
            try:
                record_approval(session, item_code, payload_to_store, canonical_hash, channel="WEB")
                session.commit()
            except IntegrityError:
                session.rollback()

        item.status = ItemStatus.APPROVED.value
        session.commit()

    if params_payload_hash:
        return JSONResponse(content={"status": "approved", "item_code": item_code, "payload_hash": canonical_hash})

    return RedirectResponse(
        url=f"/ui/items/{item_code}?notice=approved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/draft/{item_code}/update")
def api_update_draft_text(
    item_code: str,
    listing_text: str | None = Form(default=None),
    edited_listing_text: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    text = edited_listing_text if edited_listing_text is not None else listing_text
    if text is None:
        raise HTTPException(status_code=422, detail="Missing listing text")
    with get_db_session() as session:
        item = session.execute(select(Item).where(Item.code == item_code)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        # ТЗ «Склад» §1.3.2: описание пишется в item.description через stock;
        # сброс в DRAFT и синхронизация lot — по правилам update_stock_item.
        update_stock_item(session, item_code, {"description": text.strip()})
        session.commit()

    return RedirectResponse(
        url=f"/ui/draft/{item_code}?notice=updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/ui/archive", response_class=RedirectResponse)
def screen_archive(
    request: Request,
    notice: str | None = None,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ §3.4: /ui/archive -> /ui/stock?group=archive (301)."""
    return RedirectResponse(
        url="/ui/stock?group=archive",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )






