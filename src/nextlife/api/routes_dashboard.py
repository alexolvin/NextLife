"""Dashboard and Attention route handler (TZ §3.4, §10).

Rule K-1: Module must strictly be <= 400 lines.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from nextlife.api.routes import check_auth, templates
from nextlife.services.dashboard import get_attention_cards, get_dashboard_metrics
from nextlife.storage.db import get_db_session

dashboard_router = APIRouter(tags=["dashboard"])


@dashboard_router.get("/api/dashboard")
def api_dashboard_metrics(
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Return JSON representation of dashboard attention metrics."""
    with get_db_session() as session:
        return get_dashboard_metrics(session)


@dashboard_router.get("/ui/dashboard", response_class=RedirectResponse)
def ui_dashboard_redirect(
    request: Request,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ §3.4: /ui/dashboard -> /ui/attention (301)."""
    q = str(request.url.query)
    return RedirectResponse(
        url=f"/ui/attention{('?' + q) if q else ''}",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@dashboard_router.get("/ui/attention", response_class=HTMLResponse)
def ui_attention_page(
    request: Request,
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render the Attention screen with 10 prioritized cards (TZ §10)."""
    try:
        with get_db_session() as session:
            cards = get_attention_cards(session)
    except Exception:
        cards = []

    total_attention = sum(c["count"] for c in cards)

    return templates.TemplateResponse(
        request=request,
        name="attention.html",
        context={
            "cards": cards,
            "total_attention": total_attention,
            "active_tab": "attention",
        },
    )
