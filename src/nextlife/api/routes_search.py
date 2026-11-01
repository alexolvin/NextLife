"""Routes for unified search across items, listings, and messages (TZ §9)."""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from nextlife.api.routes import check_auth, templates
from nextlife.services.search import search_unified
from nextlife.storage.db import get_db_session

router = APIRouter()


@router.get("/api/search")
def api_search(
    q: str = Query(default="", description="Search query"),
    scope: str = Query(default="all", pattern="^(items|listings|messages|all)$"),
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Unified search API."""
    with get_db_session() as session:
        return search_unified(session, query=q, scope=scope, limit=limit)


@router.get("/ui/search", response_class=HTMLResponse)
def ui_search(
    request: Request,
    q: str = Query(default="", description="Search query"),
    scope: str = Query(default="all", pattern="^(items|listings|messages|all)$"),
    _auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render unified search page."""
    with get_db_session() as session:
        results = search_unified(session, query=q, scope=scope, limit=30) if q else None

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "query": q,
            "scope": scope,
            "results": results,
            "active_tab": "search",
        },
    )
