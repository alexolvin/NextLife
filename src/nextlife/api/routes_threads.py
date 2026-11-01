"""Router for messaging threads screen (T-056, Section V.2 item 8).

DoD:
- Display threads across channels.
- Side-by-side original and Russian translation.
- Three action buttons: SEND, EDIT, DECLINE.
- Prominent red badge for fraud_suspected visible without scrolling.
- Module length <= 400 lines.
- Zero raw HTML in python.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from nextlife.api.routes import check_auth, templates
from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.translation import translate_text
from nextlife.services.escalation import execute_owner_command
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Listing, Lot, Message, Thread

threads_router = APIRouter()


def _format_thread_data(session: Session, th: Thread) -> dict[str, Any]:
    lot_code = "???"
    if th.listing_id:
        listing = session.get(Listing, th.listing_id)
        if listing:
            lot = session.scalar(select(Lot).where(Lot.item_id == listing.item_code))
            lot_code = lot.code if lot else listing.item_code

    msgs = (
        session.execute(
            select(Message)
            .where(Message.thread_id == th.id)
            .order_by(Message.sent_at.asc())
        )
        .scalars()
        .all()
    )

    thread_fraud_suspected = False
    thread_fraud_reasons: list[str] = []
    messages_data = []

    for m in msgs:
        ev = m.source_evidence or {}
        msg_fraud = bool(ev.get("fraud_suspected", False))
        if msg_fraud:
            thread_fraud_suspected = True
            for r in ev.get("fraud_reasons", []):
                if r not in thread_fraud_reasons:
                    thread_fraud_reasons.append(r)

        # Translate original if text_ru is missing
        text_ru = m.text_ru
        if not text_ru and m.text_original:
            text_ru = translate_text(m.text_original, source_lang=m.lang_original, target_lang="ru")

        sent_time_str = m.sent_at.strftime("%H:%M") if m.sent_at else ""

        messages_data.append({
            "id": m.id,
            "direction": m.direction,
            "text_original": m.text_original,
            "lang_original": m.lang_original,
            "text_ru": text_ru,
            "at": sent_time_str,
            "answered_by": m.answered_by,
            "escalated": m.escalated,
            "fraud_suspected": msg_fraud,
            "prepared_response": ev.get("prepared_response"),
            "escalation_status": ev.get("escalation_status"),
        })

    counterparty_short = th.counterparty.split("@")[0] if "@" in th.counterparty else th.counterparty[:15]

    return {
        "id": th.id,
        "lot_code": lot_code,
        "channel": th.channel,
        "counterparty": th.counterparty,
        "counterparty_short": counterparty_short,
        "lang": th.lang,
        "status": th.status,
        "fraud_suspected": thread_fraud_suspected,
        "fraud_reasons": thread_fraud_reasons,
        "messages": messages_data,
    }


@threads_router.get("/ui/inbox", response_class=RedirectResponse)
def screen_inbox_redirect(
    request: Request,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ §3.4: /ui/inbox -> /ui/messages (301)."""
    q = str(request.url.query)
    return RedirectResponse(
        url=f"/ui/messages{('?' + q) if q else ''}",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@threads_router.get("/ui/messages", response_class=HTMLResponse)
@threads_router.get("/ui/threads", response_class=HTMLResponse)
def screen_threads(
    request: Request,
    thread_id: str | None = None,
    notice: str | None = None,
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    with get_db_session() as session:
        threads_db = session.execute(
            select(Thread).order_by(desc(Thread.updated_at))
        ).scalars().all()

        threads_data = [_format_thread_data(session, th) for th in threads_db]

        # Prioritize threads with fraud or escalations
        threads_data.sort(
            key=lambda t: (
                1 if t["fraud_suspected"] else 0,
                1 if any(m["escalated"] and m["answered_by"] is None for m in t["messages"]) else 0,
            ),
            reverse=True,
        )

        selected_thread = None
        if threads_data:
            if thread_id:
                selected_thread = next((t for t in threads_data if t["id"] == thread_id), threads_data[0])
            else:
                selected_thread = threads_data[0]

    return templates.TemplateResponse(
        request=request,
        name="threads.html",
        context={
            "threads": threads_data,
            "selected_thread": selected_thread,
            "active_tab": "inbox",
            "notice": notice,
        },
    )


@threads_router.post("/api/threads/{message_id}/command")
def api_thread_message_command(
    message_id: str,
    command: str = Form(...),
    thread_id: str | None = Form(None),
    custom_text: str | None = Form(None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        execute_owner_command(
            session=session,
            message_id=message_id,
            command=command,
            custom_text=custom_text,
            actor="OWNER",
        )
        session.commit()

    redirect_url = f"/ui/threads?notice=command_{command.lower()}"
    if thread_id:
        redirect_url += f"&thread_id={thread_id}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@threads_router.post("/api/threads/reply")
def api_thread_manual_reply(
    thread_id: str = Form(...),
    reply_text: str = Form(...),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        thread = session.get(Thread, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        out_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            direction="OUT",
            transport=thread.channel,
            text_original=reply_text.strip(),
            lang_original=thread.lang,
            sent_at=datetime.now(UTC),
            answered_by="OWNER",
            source_evidence={"manual_reply": True},
        )
        session.add(out_msg)

        enqueue_outbox(
            session=session,
            transport=thread.channel,
            body=reply_text.strip(),
            idempotency_key=f"manual-reply-{uuid.uuid4().hex}",
            recipient_hint=thread.counterparty,
        )
        session.commit()

    return RedirectResponse(
        url=f"/ui/threads?thread_id={thread_id}&notice=reply_sent",
        status_code=status.HTTP_303_SEE_OTHER,
    )
