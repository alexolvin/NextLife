"""API and UI routes for lot shipping options constructor (TZ v2.0 Section V.2 п.6, III.2).

DoD:
- Constructor with auto tariff selection, manual override, live recalculations.
- Adding and removing options recalculates buyer_total and seller_net without reload.
- Manual price override is explicitly flagged.
- Module length <= 400 lines.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from nextlife.api.routes import check_auth
from nextlife.core.shipping_policy import normalize_shipping_policy
from nextlife.services.shipping_options import (
    configure_lot_delivery_options,
    get_lot_delivery_summary,
)
from nextlife.storage.db import get_db_session

shipping_router = APIRouter(tags=["shipping"])


def _normalize_policy_or_400(shipping_policy: str | None) -> str | None:
    """TZ-NextLife-UI-N2 §4 V4 (P-E10): единый словарь политики доставки."""
    if shipping_policy is None:
        return None
    policy = normalize_shipping_policy(shipping_policy)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown shipping policy {shipping_policy!r}; available: BUYER_PAYS, SELLER_PAYS, PICKUP_ONLY",
        )
    return policy


class ShippingOptionInput(BaseModel):
    carrier_key: str
    service_name: str | None = None
    price: float | None = None
    is_manual_price: bool | None = None
    is_enabled: bool = True


class SaveShippingOptionsPayload(BaseModel):
    shipping_policy: str | None = None
    options: list[ShippingOptionInput]


@shipping_router.get("/api/lots/{code}/shipping-options")
def api_get_shipping_options(
    code: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Get delivery options configuration and live metrics for a lot."""
    with get_db_session() as session:
        try:
            return get_lot_delivery_summary(session, code)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@shipping_router.post("/api/lots/{code}/shipping-options")
async def api_save_shipping_options(
    code: str,
    request: Request,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Save delivery options configuration for a lot (supports list, envelope or single option)."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    shipping_policy = None
    raw_options: list[dict[str, Any]] = []

    if isinstance(body, dict):
        if "options" in body:
            shipping_policy = body.get("shipping_policy")
            for opt in body.get("options", []):
                if isinstance(opt, dict):
                    raw_options.append(opt)
        else:
            # Single option dictionary
            carrier_key = (
                body.get("carrier_key")
                or body.get("carrier")
                or body.get("carrier_name")
                or "pickup"
            )
            price = body.get("price") if body.get("price") is not None else body.get("cost")
            raw_options.append(
                {
                    "carrier_key": str(carrier_key).lower().strip(),
                    "service_name": body.get("service_name"),
                    "price": price,
                    "is_manual_price": body.get("is_manual_price"),
                    "is_enabled": body.get("is_enabled", True),
                }
            )
            shipping_policy = body.get("shipping_policy")
    elif isinstance(body, list):
        raw_options = body

    shipping_policy = _normalize_policy_or_400(shipping_policy)

    with get_db_session() as session:
        try:
            configure_lot_delivery_options(
                session=session,
                lot_code=code,
                options=raw_options,
                shipping_policy=shipping_policy,
            )
            session.commit()
            return get_lot_delivery_summary(session, code)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@shipping_router.get("/api/lots/{code}/buyer-total")
def api_get_buyer_total(
    code: str,
    carrier: str | None = None,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Calculate buyer total for a lot with delivery cost included."""
    with get_db_session() as session:
        try:
            summary = get_lot_delivery_summary(session, code)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        options = summary.get("options", [])
        selected_opt = None
        if carrier:
            selected_opt = next((o for o in options if o["carrier_key"].lower() == carrier.lower()), None)
        if not selected_opt and options:
            selected_opt = options[0]

        item_price = summary.get("item_price", 0.0)
        shipping_cost = selected_opt["price"] if selected_opt else 0.0
        buyer_total = selected_opt["buyer_total"] if selected_opt else item_price
        seller_net = selected_opt["seller_net"] if selected_opt else item_price

        return {
            "lot_code": code,
            "item_code": summary.get("item_code"),
            "item_price": item_price,
            "currency": "CZK",
            "shipping_cost": shipping_cost,
            "buyer_total": buyer_total,
            "seller_net": seller_net,
            "shipping_policy": summary.get("shipping_policy", "BUYER_PAYS"),
            "selected_carrier": selected_opt["carrier_key"] if selected_opt else None,
            "options": options,
        }


@shipping_router.get("/ui/lots/{code}/shipping")
def ui_shipping_options_page(
    code: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """ТЗ «Склад» §6: standalone shipping screen removed — redirect to the
    «Доставка» block on the Склад item screen."""
    return RedirectResponse(url=f"/ui/stock/{code}#delivery", status_code=status.HTTP_302_FOUND)


@shipping_router.post("/ui/lots/{code}/shipping")
def ui_save_shipping_options(
    request: Request,
    code: str,
    payload: str = Form(...),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Handle UI form submission for shipping options."""
    try:
        parsed = json.loads(payload)
    except Exception:
        parsed = {}

    shipping_policy = _normalize_policy_or_400(parsed.get("shipping_policy", "BUYER_PAYS"))
    options = parsed.get("options", [])

    with get_db_session() as session:
        try:
            configure_lot_delivery_options(
                session=session,
                lot_code=code,
                options=options,
                shipping_policy=shipping_policy,
            )
            session.commit()
            summary = get_lot_delivery_summary(session, code)
            item_code = summary["item_code"]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RedirectResponse(
        url=f"/ui/items/{item_code}?notice=shipping_options_saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )
