"""Research service wiring: API + UI screens (TZ-NextLife-UI-N2 §4 V5).

P-R07: кнопка запуска на шаге 4 мастера, в карточке и в черновике; запуск
создаёт research_run (синхронно); отчёт — диапазон цен в валюте предмета
только при выборке не меньше min_comparables, с числом аналогов и
ссылками; иначе — явное сообщение. Диапазон без источников не выводится.
P-R08: 2-3 редактируемых чешских варианта по найденным аналогам; выбор
сохраняется через существующую семантику черновика (final_text, P-E12).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from nextlife.api.routes import check_auth
from nextlife.core.config import load_config
from nextlife.services.research import run_research
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ResearchResult

research_router = APIRouter(tags=["research"])


@research_router.post("/api/items/{item_code}/research")
def api_run_research(item_code: str, auth: bool = Depends(check_auth)) -> JSONResponse:
    """Запустить разведку (API): синхронный run, ответ — число аналогов."""
    with get_db_session() as session:
        item = session.get(Item, item_code)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        config = load_config()
        try:
            run_id = run_research(session, item.code, item.title, config)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Research run failed: {exc}",
            ) from exc
        found = (
            session.execute(
                select(ResearchResult).where(ResearchResult.run_id == run_id)
            )
            .scalars()
            .all()
        )
    return JSONResponse(content={"run_id": run_id, "total_found": len(found)})


@research_router.api_route(
    "/ui/items/{item_code}/research", methods=["GET", "POST"]
)
@research_router.api_route(
    "/ui/stock/{item_code}/research", methods=["GET", "POST"]
)
async def ui_run_research(
    request: Request,
    item_code: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Запустить разведку (UI-кнопка/ссылка) и вернуться на экран предмета."""
    next_url = ""
    query = ""
    if request.method == "POST":
        try:
            form = await request.form()
            next_url = str(form.get("next") or "")
            query = str(form.get("q") or "").strip()
        except Exception:
            pass
    if not next_url:
        next_url = str(request.query_params.get("next") or "")
    if not query:
        query = str(request.query_params.get("q") or "").strip()

    with get_db_session() as session:
        item = session.get(Item, item_code)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        search_query = query or item.title
        config = load_config()
        notice = "research_done"
        reason = ""
        try:
            run_research(session, item.code, search_query, config)
            session.commit()
        except Exception as exc:
            session.rollback()
            import logging
            logging.getLogger(__name__).exception("Failed to run research on item %s: %s", item.code, exc)
            notice = "research_failed"
            reason = str(exc)
    target = next_url if next_url.startswith("/ui/") else f"/ui/stock/{item_code}#price"
    from urllib.parse import quote
    reason_param = f"&reason={quote(reason[:120])}" if reason else ""
    if "#" in target:
        base, fragment = target.split("#", 1)
        sep = "&" if "?" in base else "?"
        final_url = f"{base}{sep}notice={notice}{reason_param}#{fragment}"
    else:
        sep = "&" if "?" in target else "?"
        final_url = f"{target}{sep}notice={notice}{reason_param}"
    return RedirectResponse(
        url=final_url, status_code=status.HTTP_303_SEE_OTHER
    )
