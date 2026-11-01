"""UI routes for Deal Screen (TZ v2.0 Section V.2 item 9 / Task T-067).

DoD:
- Display deal status, counterparty, agreed price, payment & delivery method.
- Checklists (packaging & inspection).
- Dispatch evidence upload & completeness status.
- Handover protocol act generation & preview.
- Settlement calculation.
- Modal confirmation for state-changing dispatch action.
- Mobile friendly layout (360px+).
- Module length <= 400 lines.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from nextlife.api.routes import check_auth, templates
from nextlife.core.exceptions import NextLifeError
from nextlife.deals.handover_protocol import generate_handover_protocol, get_handover_protocol
from nextlife.services.dispatch_evidence import dispatch_deal, get_evidence_status
from nextlife.services.inspection import get_checklist_items, load_inspection_checklist
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Handover, Item, Lot, Party, Settlement

deal_screen_router = APIRouter(tags=["deal-screen"])


@deal_screen_router.get("/ui/deals/{deal_id}", response_class=HTMLResponse)
def get_deal_screen(
    request: Request,
    deal_id: str,
    authenticated: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render full deal management screen for mobile and desktop."""
    with get_db_session() as session:
        deal = session.get(Deal, deal_id)
        if not deal:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Deal '{deal_id}' not found")

        lot = session.get(Lot, deal.lot_id) if deal.lot_id else None
        lot_code = lot.code if lot else "—"

        item = session.get(Item, lot.item_id) if (lot and lot.item_id) else None
        # TZ-NextLife-UI-N2 §4 V4 (P-E09): вместо «N/A» — деградированный,
        # но осмысленный текст (Э-1: ярлык соответствует содержимому).
        item_title = item.title if item else ((lot.notes if lot else None) or "Предмет не привязан к сделке")
        category = item.category_key if item else "general"
        # ТЗ «Склад» §3.6: место на складе — первой строкой карточки предмета.
        storage_location = item.storage_location if item else None

        party = session.get(Party, deal.party_id) if deal.party_id else None
        party_name = party.name if party and party.name else "Частное лицо"
        party_contact = (party.email or party.phone or "Внутренний чат площадки") if party else "Контакт не указан"

        settlement = session.query(Settlement).filter(Settlement.deal_id == deal.id).first()
        if not settlement:
            settlement = Settlement(
                deal_id=deal.id,
                gross_amount=deal.agreed_price,
                platform_fee=Decimal("0.00"),
                delivery_cost=Decimal("0.00"),
                packaging_cost=Decimal("0.00"),
                net_payout=deal.agreed_price,
                currency=deal.currency,
            )

        handover = session.query(Handover).filter(Handover.deal_id == deal.id).first()
        protocol_content = get_handover_protocol(session, deal.id)

        evidence_status = get_evidence_status(session, deal.id)
        inspection_data = load_inspection_checklist(category)
        inspection_points = get_checklist_items(category, lang="ru")
        category_name = inspection_data.get("category_name_ru", "Осмотр товара")

        return templates.TemplateResponse(
            request=request,
            name="deal.html",
            context={
                "deal": deal,
                "lot": lot,
                "lot_code": lot_code,
                "item_title": item_title,
                "storage_location": storage_location,
                "party_name": party_name,
                "party_contact": party_contact,
                "settlement": settlement,
                "handover": handover,
                "protocol_content": protocol_content,
                "evidence_status": evidence_status,
                "category_name": category_name,
                "inspection_points": inspection_points,
            },
        )


@deal_screen_router.get("/ui/lots/{lot_code}/deal")
def get_lot_deal_redirect(
    lot_code: str,
    authenticated: bool = Depends(check_auth),
) -> RedirectResponse:
    """Redirect lot to its active deal screen."""
    with get_db_session() as session:
        lot = session.query(Lot).filter(Lot.code == lot_code).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Lot {lot_code} not found")

        deal = session.query(Deal).filter(Deal.lot_id == lot.id).order_by(Deal.created_at.desc()).first()
        if not deal:
            raise HTTPException(status_code=404, detail=f"No deal associated with lot {lot_code}")

        return RedirectResponse(url=f"/ui/deals/{deal.id}", status_code=status.HTTP_303_SEE_OTHER)


@deal_screen_router.post("/ui/deals/{deal_id}/protocol/generate")
def ui_generate_protocol(
    deal_id: str,
    authenticated: bool = Depends(check_auth),
) -> RedirectResponse:
    """Generate or re-generate handover protocol act."""
    with get_db_session() as session:
        try:
            generate_handover_protocol(session=session, deal_id=deal_id, actor="OWNER")
            session.commit()
        except NextLifeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return RedirectResponse(url=f"/ui/deals/{deal_id}", status_code=status.HTTP_303_SEE_OTHER)


@deal_screen_router.post("/ui/deals/{deal_id}/dispatch")
def ui_dispatch_deal(
    deal_id: str,
    tracking_number: str = Form(...),
    authenticated: bool = Depends(check_auth),
) -> RedirectResponse:
    """Dispatch deal parcel from UI modal confirmation."""
    with get_db_session() as session:
        try:
            dispatch_deal(
                session=session,
                deal_id=deal_id,
                tracking_number=tracking_number,
                carrier="Zásilkovna",
                actor="OWNER",
            )
            session.commit()
        except NextLifeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return RedirectResponse(url=f"/ui/deals/{deal_id}", status_code=status.HTTP_303_SEE_OTHER)
