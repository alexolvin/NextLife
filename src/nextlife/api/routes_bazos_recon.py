"""Bazoš recon and publish endpoints.

Rule K-1: Module must strictly be <= 400 lines.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from nextlife.api.routes import check_auth

bazos_recon_router = APIRouter(tags=["bazos_recon"])


@bazos_recon_router.post("/api/recon/bazos")
def recon_bazos_endpoint(auth: bool = Depends(check_auth)) -> Any:
    """V0.6 recon: navigate to the Bazoš login page and dump its form (read-only)."""
    from nextlife.services.web_publish import recon_bazos as _recon

    return _recon()


@bazos_recon_router.post("/api/recon/bazos/publish")
def recon_bazos_publish_endpoint(auth: bool = Depends(check_auth)) -> Any:
    """V0.6 recon: navigate the Bazoš publish flow and dump the listing form (read-only)."""
    from nextlife.services.web_publish import recon_bazos_publish as _recon

    return _recon()


@bazos_recon_router.post("/api/publish/bazos/login/start")
def bazos_login_start_endpoint(auth: bool = Depends(check_auth)) -> Any:
    """Step 1 of Bazoš login: fill the login form, submit, send the SMS."""
    from nextlife.services.bazos_publish import bazos_login_start as _start

    return _start()


class _BazosLoginConfirmBody(BaseModel):
    code: str


@bazos_recon_router.post("/api/publish/bazos/login/confirm")
def bazos_login_confirm_endpoint(body: _BazosLoginConfirmBody, auth: bool = Depends(check_auth)) -> Any:
    """Step 2 of Bazoš login: enter the owner's SMS code and complete login."""
    from nextlife.services.bazos_publish import bazos_login_confirm as _confirm

    return _confirm(body.code)


@bazos_recon_router.post("/api/publish/bazos/login/check")
def bazos_check_login_endpoint(auth: bool = Depends(check_auth)) -> Any:
    """Check if the persistent browser profile has a valid Bazoš session."""
    from nextlife.services.bazos_publish import bazos_check_login as _check

    return _check()


@bazos_recon_router.post("/api/publish/bazos/recon")
def bazos_recon_publish_endpoint(auth: bool = Depends(check_auth)) -> Any:
    """Recon the Bazoš publish form structure (read-only)."""
    from nextlife.services.bazos_publish import bazos_recon_publish as _recon

    return _recon()


@bazos_recon_router.post("/api/publish/bazos/inzeraty")
def bazos_list_inzeraty_endpoint(auth: bool = Depends(check_auth)) -> Any:
    """List the owner's Bazoš inzeraty (verifies a publish actually created a listing)."""
    from nextlife.services.bazos_publish import bazos_list_inzeraty as _list

    return _list()


class _BazosListingBody(BaseModel):
    title: str
    description: str
    price: str
    category: str = ""
    location: str = ""
    seller_name: str = ""
    dry_run: bool = False
    channel: str = "web"


@bazos_recon_router.post("/api/publish/bazos/listing")
def bazos_publish_listing_endpoint(body: _BazosListingBody, auth: bool = Depends(check_auth)) -> Any:
    """Publish a listing on Bazoš. Default channel is the web form."""
    if body.channel == "android":
        from nextlife.services.bazos_android_publish import (
            bazos_android_publish_listing as _android,
        )

        return _android(
            title=body.title,
            description=body.description,
            price=body.price,
            category=body.category,
            location=body.location,
            seller_name=body.seller_name,
            dry_run=body.dry_run,
        )
    from nextlife.services.bazos_publish import bazos_publish_listing as _publish

    return _publish(
        title=body.title,
        description=body.description,
        price=body.price,
        category=body.category,
        location=body.location,
        seller_name=body.seller_name,
        dry_run=body.dry_run,
    )


@bazos_recon_router.get("/api/recon/bazos/{name}.png")
def recon_bazos_screenshot(name: str, auth: bool = Depends(check_auth)) -> Any:
    """Serve a Bazoš recon screenshot by name (e.g. ``bazos-1-home``)."""
    from nextlife.services.web_publish import _recon_dir

    if "/" in name or "\\" in name or name.startswith(".") or not name:
        raise HTTPException(status_code=400, detail="invalid screenshot name")
    path = _recon_dir() / f"{name}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(str(path))
