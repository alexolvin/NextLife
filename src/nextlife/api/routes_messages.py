"""Web routes and API endpoints for Messages section (TZ §7.1, §7.2).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from nextlife.api.routes import check_auth, templates
from nextlife.services.messages import (
    MessagesServiceError,
    get_thread_data,
    list_threads,
    send_reply,
    suggest_reply,
)
from nextlife.services.notion import NotionIntegrationError, create_or_update_schedule_event
from nextlife.storage.db import get_db_session

messages_router = APIRouter(tags=["messages"])


@messages_router.get("/ui/messages", response_class=HTMLResponse)
def screen_messages_list(
    request: Request,
    filter_group: str = Query(default="all", alias="filter"),
    q: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render list of dialogs grouped by item (§7.1)."""
    with get_db_session() as session:
        threads = list_threads(session, filter_group=filter_group, q=q)
        unread_count = len([t for t in threads if t["is_unread"]])
        escalated_count = len([t for t in threads if t["escalated"]])

    return templates.TemplateResponse(
        request=request,
        name="messages.html",
        context={
            "threads": threads,
            "filter_group": filter_group,
            "q": q or "",
            "unread_count": unread_count,
            "escalated_count": escalated_count,
            "active_tab": "messages",
        },
    )


@messages_router.get("/ui/messages/{thread_id}", response_class=HTMLResponse)
def screen_thread_detail(
    request: Request,
    thread_id: str,
    view_mode: str = Query(default="ru"),  # 'ru' or 'original'
    notice: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render dialog detail screen with LLM reply suggestion and actions (§7.2)."""
    with get_db_session() as session:
        try:
            data = get_thread_data(session, thread_id)
            suggestion = suggest_reply(session, thread_id)
        except MessagesServiceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return templates.TemplateResponse(
            request=request,
            name="message_detail.html",
            context={
                "thread": data["thread"],
                "item": data["item"],
                "listing": data["listing"],
                "caps": data["caps"],
                "messages": data["messages"],
                "suggestion": suggestion,
                "view_mode": view_mode,
                "notice": notice,
                "reason": reason,
                "active_tab": "messages",
            },
        )


@messages_router.post("/ui/messages/{thread_id}/send")
def ui_send_reply(
    thread_id: str,
    text_ru: str = Form(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Translate and send reply or redirect with copy link (§7.2)."""
    with get_db_session() as session:
        try:
            res = send_reply(session, thread_id, text_ru)
            session.commit()
            if not res["supported"]:
                return RedirectResponse(
                    url=f"/ui/messages/{thread_id}?notice=unsupported_send&reason={res['translated_text']}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
        except MessagesServiceError as exc:
            return RedirectResponse(
                url=f"/ui/messages/{thread_id}?notice=err&reason={exc}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    return RedirectResponse(
        url=f"/ui/messages/{thread_id}?notice=sent",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@messages_router.post("/ui/messages/{thread_id}/schedule")
def ui_schedule_deal_event(
    thread_id: str,
    deal_id: str = Form(""),
    event_type: str = Form("shipment"),  # shipment or meeting
    dt: str = Form(""),
    location: str = Form(""),
    note: str = Form(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Schedule shipment or meeting in Notion calendar (§7.4)."""
    with get_db_session() as session:
        try:
            create_or_update_schedule_event(
                session=session,
                deal_id=deal_id,
                event_type=event_type,
                dt_iso=dt,
                location_or_carrier=location,
                note=note,
            )
            session.commit()
            return RedirectResponse(
                url=f"/ui/messages/{thread_id}?notice=notion_scheduled",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        except NotionIntegrationError as exc:
            return RedirectResponse(
                url=f"/ui/messages/{thread_id}?notice=notion_failed&reason={exc}",
                status_code=status.HTTP_303_SEE_OTHER,
            )


@messages_router.post("/api/messages/{thread_id}/suggest")
def api_suggest_reply(
    thread_id: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    with get_db_session() as session:
        return suggest_reply(session, thread_id)


@messages_router.post("/api/messages/{thread_id}/send")
def api_send_reply(
    thread_id: str,
    payload: dict[str, Any],
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    text_ru = str(payload.get("text_ru") or payload.get("text") or "").strip()
    if not text_ru:
        raise HTTPException(status_code=422, detail="Text cannot be empty")
    with get_db_session() as session:
        res = send_reply(session, thread_id, text_ru)
        session.commit()
        return res
