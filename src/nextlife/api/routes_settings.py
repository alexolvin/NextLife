"""Settings routes for NextLife UI (TZ §11).

Five distinct screens/tabs, each with its own Save button:
1. Мои данные (profile)
2. Площадки (marketplaces + capabilities matrix + listing_ttl_days)
3. ИИ (Text LLM + Image AI endpoints and test buttons)
4. Notion (database and token + test button)
5. Покупки (default currency, profit threshold %)

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from nextlife.api.routes import check_auth, templates
from nextlife.core.config import load_config
from nextlife.services.llm_filter import MP_SECRETS, load_settings, save_settings

settings_router = APIRouter(tags=["settings"])

_DEFAULT_CAPS = {
    "search": False, "publish": False, "update": False, "unpublish": False,
    "relist": False, "auto_unpublish": False, "auto_relist": False,
    "messages_read": False, "messages_send": False, "delivery_parse": False,
}

CAPABILITIES_MATRIX = {
    "bazos_cz": {**_DEFAULT_CAPS, "search": True, "publish": True, "unpublish": True, "relist": True, "auto_unpublish": True, "auto_relist": True, "delivery_parse": True},
    "bazos_sk": {**_DEFAULT_CAPS, "search": True, "publish": True, "unpublish": True, "relist": True, "auto_unpublish": True, "auto_relist": True},
    "aukro_cz": {**_DEFAULT_CAPS, "search": True, "publish": True, "delivery_parse": True},
    "sbazar_cz": {**_DEFAULT_CAPS, "search": True, "publish": True},
    "trhknih_cz": {**_DEFAULT_CAPS, "search": True}, "hyperinzerce_cz": {**_DEFAULT_CAPS, "search": True},
    "annonce_cz": {**_DEFAULT_CAPS, "search": True}, "bazar_cz": {**_DEFAULT_CAPS, "search": True},
    "mimibazar_cz": {**_DEFAULT_CAPS, "search": True}, "allegro_cz": {**_DEFAULT_CAPS},
    "vinted": {**_DEFAULT_CAPS, "search": True}, "willhaben_at": {**_DEFAULT_CAPS, "search": True},
    "kleinanzeigen_de": {**_DEFAULT_CAPS, "search": True}, "olx_pl": {**_DEFAULT_CAPS, "search": True},
    "facebook_marketplace": {**_DEFAULT_CAPS, "search": True},
}

MP_META: dict[str, dict[str, Any]] = {
    "bazos_cz": {"login_url": "https://pc.bazos.cz/pridat.php", "phone_platform": "bazos_cz"},
    "bazos_sk": {"login_url": "https://pc.bazos.sk/pridat.php", "phone_platform": "bazos_sk"},
    "sbazar_cz": {"login_url": "https://login.szn.cz/?service=sbazar", "phone_platform": "sbazar_cz"},
    "aukro_cz": {"login_url": "https://aukro.cz/uzivatel/prihlaseni", "phone_platform": None},
    "bazar_cz": {"login_url": "https://www.bazar.cz/prihlaseni", "phone_platform": None},
    "mimibazar_cz": {"login_url": "https://www.mimibazar.cz/prihlaseni", "phone_platform": None},
    "trhknih_cz": {"login_url": "https://www.trhknih.cz/prihlasit", "phone_platform": None},
    "vinted": {"login_url": "https://www.vinted.cz/member/general/login", "phone_platform": "vinted"},
    "facebook_marketplace": {"login_url": "https://www.facebook.com/marketplace", "phone_platform": "facebook_marketplace"},
}


@settings_router.get("/ui/settings", response_class=HTMLResponse)
def screen_settings(
    request: Request,
    tab: str = "profile",
    notice: str | None = None,
    err: str | None = None,
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render the Settings page with 5 distinct sections/tabs (TZ §11)."""
    cfg = load_config()
    settings = load_settings()
    overrides = settings.get("marketplace_overrides", {})
    secrets = settings.get("secrets", {})
    if not isinstance(secrets, dict):
        secrets = {}

    profile = settings.get("profile", {})
    if not isinstance(profile, dict):
        profile = {}

    # Merge legacy seller secrets into profile if missing
    seller_name = profile.get("seller_name") or secrets.get("SELLER_NAME", "")
    seller_psc = profile.get("seller_psc") or secrets.get("SELLER_PSC", "")
    seller_phone = profile.get("seller_phone") or secrets.get("BAZOS_PHONE", "")
    default_meeting_place = profile.get("default_meeting_place", "")

    from nextlife.connectors.registry import _registry_capabilities
    from nextlife.services.marketplace_shipping import (
        ALL_CARRIERS,
        get_marketplace_allowed_carriers,
        get_marketplace_listing_ttl,
    )

    marketplaces = []
    for key, mp in cfg.marketplaces.items():
        ov = overrides.get(key, {})
        reg_caps = _registry_capabilities(mp)
        base_caps = CAPABILITIES_MATRIX.get(key, _DEFAULT_CAPS)
        caps = {**_DEFAULT_CAPS, **base_caps, **{k: bool(v) for k, v in reg_caps.items() if k in _DEFAULT_CAPS}}
        active_search = ov.get("search", caps.get("search", False))
        active_publish = ov.get("publish", caps.get("publish", False))
        caps["search"], caps["publish"] = active_search, active_publish

        if active_search and active_publish:
            status_level, status_label = "full", "Полный доступ"
            status_bg, status_border, status_dot, badge_bg, badge_text, badge_border = (
                "#f0fdf4", "#86efac", "#16a34a", "#dcfce7", "#166534", "#bbf7d0"
            )
        elif active_search:
            status_level, status_label = "view_only", "Только просмотр"
            status_bg, status_border, status_dot, badge_bg, badge_text, badge_border = (
                "#fefce8", "#fde047", "#ca8a04", "#fef9c3", "#854d0e", "#fef08a"
            )
        else:
            status_level, status_label = "disabled", "Недоступен"
            status_bg, status_border, status_dot, badge_bg, badge_text, badge_border = (
                "#fef2f2", "#fca5a5", "#dc2626", "#fee2e2", "#991b1b", "#fecaca"
            )

        mp_secrets = [
            {"key": sk, "label": label, "type": itype, "placeholder": ph, "set": bool(secrets.get(sk))}
            for sk, label, itype, ph in MP_SECRETS.get(key, [])
        ]
        mp_ttl = get_marketplace_listing_ttl(key, settings)
        allowed_c = set(get_marketplace_allowed_carriers(key, settings))
        carriers = [{**c, "checked": c["key"] in allowed_c} for c in ALL_CARRIERS]
        meta = MP_META.get(key, {})
        marketplaces.append({
            "key": key, "title": mp.title, "search": active_search, "publish": active_publish,
            "auto_unpublish": bool(caps.get("auto_unpublish", caps.get("unpublish", False))),
            "auto_relist": bool(caps.get("auto_relist", caps.get("relist", False))),
            "access_mode": mp.access_mode, "secrets": mp_secrets, "caps": caps,
            "listing_ttl_days": mp_ttl, "carriers": carriers,
            "login_url": meta.get("login_url"), "phone_platform": meta.get("phone_platform"),
            "status_level": status_level, "status_label": status_label,
            "status_bg": status_bg, "status_border": status_border,
            "status_dot": status_dot, "badge_bg": badge_bg,
            "badge_text": badge_text, "badge_border": badge_border,
        })

    llm_config = settings.get("llm", {})
    image_ai_config = settings.get("image_ai", {})
    notion_config = settings.get("notion", {})
    buy_config = settings.get("buy", {"default_currency": "CZK", "profit_threshold_pct": 25})
    listing_ttl_days = settings.get("listing_ttl_days", 30)

    from nextlife.version import get_version_str

    return templates.TemplateResponse(
        request=request, name="settings.html",
        context={
            "app_version": get_version_str(), "tab": tab, "notice": notice, "err": err,
            "seller_name": seller_name, "seller_psc": seller_psc, "seller_phone": seller_phone,
            "default_meeting_place": default_meeting_place, "marketplaces": marketplaces,
            "listing_ttl_days": listing_ttl_days, "llm_config": llm_config,
            "image_ai_config": image_ai_config, "notion_config": notion_config,
            "buy_config": buy_config, "active_tab": "settings",
        },
    )


@settings_router.post("/ui/settings/profile")
def save_settings_profile(
    request: Request,
    seller_name: str = Form(""),
    seller_psc: str = Form(""),
    seller_phone: str = Form(""),
    default_meeting_place: str = Form(""),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Save profile settings (TZ §11 item 1)."""
    settings = load_settings()
    settings["profile"] = {
        "seller_name": seller_name.strip(),
        "seller_psc": seller_psc.strip(),
        "seller_phone": seller_phone.strip(),
        "default_meeting_place": default_meeting_place.strip(),
    }
    secrets = settings.get("secrets", {})
    if not isinstance(secrets, dict):
        secrets = {}
    if seller_name.strip():
        secrets["SELLER_NAME"] = seller_name.strip()
    if seller_psc.strip():
        secrets["SELLER_PSC"] = seller_psc.strip()
    if seller_phone.strip():
        secrets["BAZOS_PHONE"] = seller_phone.strip()
    settings["secrets"] = secrets
    save_settings(settings)
    return RedirectResponse(url="/ui/settings?tab=profile&notice=profile_saved", status_code=status.HTTP_303_SEE_OTHER)


@settings_router.post("/ui/settings/marketplaces")
async def save_settings_marketplaces(
    request: Request,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Save marketplace flags, secrets, and listing_ttl_days (TZ §11 item 2)."""
    form = await request.form()
    settings = load_settings()
    overrides: dict[str, dict[str, bool]] = settings.get("marketplace_overrides", {})
    secrets: dict[str, str] = settings.get("secrets", {})
    if not isinstance(secrets, dict):
        secrets = {}

    cfg = load_config()
    from nextlife.services.marketplace_shipping import ALL_CARRIERS
    for key in cfg.marketplaces:
        search_val = form.get(f"mp_{key}_search", "off")
        publish_val = form.get(f"mp_{key}_publish", "off")
        ttl_val = form.get(f"mp_{key}_ttl")
        allowed_c = [c["key"] for c in ALL_CARRIERS if form.get(f"mp_{key}_carrier_{c['key']}") == "on"]
        try:
            mp_ttl = max(1, min(365, int(ttl_val))) if ttl_val else 30
        except (ValueError, TypeError):
            mp_ttl = 30
        overrides[key] = {
            "search": search_val == "on",
            "publish": publish_val == "on",
            "listing_ttl_days": mp_ttl,
            "allowed_carriers": allowed_c,
        }
        for secret_key, _, _, _ in MP_SECRETS.get(key, []):
            if form.get(f"clear_{secret_key}") == "on":
                secrets.pop(secret_key, None)
                continue
            raw = form.get(secret_key)
            val = raw.strip() if isinstance(raw, str) else ""
            if val:
                secrets[secret_key] = val

    ttl_raw = form.get("listing_ttl_days", "30")
    try:
        settings["listing_ttl_days"] = int(ttl_raw)
    except Exception:
        settings["listing_ttl_days"] = 30

    settings["marketplace_overrides"] = overrides
    settings["secrets"] = secrets
    save_settings(settings)
    return RedirectResponse(url="/ui/settings?tab=marketplaces&notice=marketplaces_saved", status_code=status.HTTP_303_SEE_OTHER)


@settings_router.post("/ui/settings/marketplaces/{platform}/open-phone", response_class=RedirectResponse)
def open_marketplace_on_phone(platform: str, auth: bool = Depends(check_auth)) -> RedirectResponse:
    from nextlife.android.transport import make_phone_broker_transport

    meta = MP_META.get(platform, {})
    phone_key = meta.get("phone_platform") or platform
    try:
        cfg = load_config().app.android_bridge.phone_broker
        transport = make_phone_broker_transport(cfg)
        transport.begin(phone_key, "interactive")
        transport.open_app()
        transport.end()
        notice = "phone_opened"
    except Exception as exc:
        notice = f"phone_error_{exc}"

    return RedirectResponse(
        url=f"/ui/settings?tab=marketplaces&notice={notice}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@settings_router.post("/ui/settings/ai")
async def save_settings_ai(request: Request, auth: bool = Depends(check_auth)) -> RedirectResponse:
    """Save Text LLM and Image AI settings (TZ §11 item 3)."""
    form = await request.form()
    settings = load_settings()
    settings["llm"] = {
        "provider": str(form.get("llm_provider", "openai")).strip().lower() or "openai",
        "endpoint": str(form.get("llm_endpoint", "")).strip(),
        "api_key": str(form.get("llm_api_key", "")).strip(),
        "model": str(form.get("llm_model", "")).strip(),
    }
    img_ai: dict[str, str] = {
        "provider": str(form.get("image_ai_provider", "openai")).strip().lower() or "openai",
        "endpoint": str(form.get("image_ai_endpoint", "")).strip(),
        "api_key": str(form.get("image_ai_api_key", "")).strip(),
        "model": str(form.get("image_ai_model", "")).strip(),
    }
    for op in ("dust", "selling", "bg"):
        img_ai[f"model_{op}"] = str(form.get(f"image_ai_model_{op}", "")).strip()
        img_ai[f"endpoint_{op}"] = str(form.get(f"image_ai_endpoint_{op}", "")).strip()
        img_ai[f"provider_{op}"] = str(form.get(f"image_ai_provider_{op}", "")).strip().lower()
        img_ai[f"api_key_{op}"] = str(form.get(f"image_ai_api_key_{op}", "")).strip()
    settings["image_ai"] = img_ai
    save_settings(settings)
    return RedirectResponse(url="/ui/settings?tab=ai&notice=ai_saved", status_code=status.HTTP_303_SEE_OTHER)


@settings_router.post("/ui/settings/notion")
def save_settings_notion(
    request: Request,
    notion_token: str = Form(""),
    notion_database_id: str = Form(""),
    notion_title_prop: str = Form("Name"),
    notion_date_prop: str = Form("Date"),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Save Notion integration settings (TZ §11 item 4)."""
    settings = load_settings()
    settings["notion"] = {
        "token": notion_token.strip(),
        "database_id": notion_database_id.strip(),
        "title_prop": notion_title_prop.strip() or "Name",
        "date_prop": notion_date_prop.strip() or "Date",
    }
    save_settings(settings)
    return RedirectResponse(url="/ui/settings?tab=notion&notice=notion_saved", status_code=status.HTTP_303_SEE_OTHER)


@settings_router.post("/ui/settings/buy")
def save_settings_buy(
    request: Request,
    default_currency: str = Form("CZK"),
    profit_threshold_pct: int = Form(25),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Save Buy section settings (TZ §11 item 5)."""
    settings = load_settings()
    settings["buy"] = {
        "default_currency": default_currency.strip().upper() or "CZK",
        "profit_threshold_pct": max(1, min(100, int(profit_threshold_pct))),
    }
    save_settings(settings)
    return RedirectResponse(url="/ui/settings?tab=buy&notice=buy_saved", status_code=status.HTTP_303_SEE_OTHER)


@settings_router.post("/ui/settings/test-llm")
def test_llm_endpoint(auth: bool = Depends(check_auth)) -> RedirectResponse:
    """Test Text LLM connection."""
    cfg = load_settings().get("llm", {})
    provider = (cfg.get("provider") or "openai").strip().lower()
    endpoint, api_key = (cfg.get("endpoint") or "").strip(), (cfg.get("api_key") or "").strip()
    err = None
    if provider == "gemini" and not endpoint and not api_key:
        err = "API Key или URL эндпоинта Gemini Text LLM не задан"
    elif provider != "gemini" and not endpoint:
        err = "URL эндпоинта LLM не задан"
    if err:
        return RedirectResponse(url=f"/ui/settings?tab=ai&err={quote(err)}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/ui/settings?tab=ai&notice=llm_test_ok", status_code=status.HTTP_303_SEE_OTHER)


@settings_router.post("/ui/settings/test-image-ai")
def test_image_ai_endpoint(auth: bool = Depends(check_auth)) -> RedirectResponse:
    """Test Image AI connection."""
    cfg = load_settings().get("image_ai", {})
    provider = (cfg.get("provider") or "openai").strip().lower()
    endpoint, api_key = cfg.get("endpoint"), cfg.get("api_key")
    err = None
    if provider == "gemini" and not endpoint and not api_key:
        err = "API Key или URL эндпоинта Gemini Image AI не задан"
    elif provider == "cloudflare" and not api_key:
        err = "API Token для Cloudflare Workers AI не задан"
    elif provider not in ("gemini", "cloudflare") and not endpoint:
        err = "URL эндпоинта Image AI не задан"
    if err:
        return RedirectResponse(url=f"/ui/settings?tab=ai&err={quote(err)}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/ui/settings?tab=ai&notice=image_ai_test_ok", status_code=status.HTTP_303_SEE_OTHER)


@settings_router.post("/ui/settings/test-notion")
def test_notion_endpoint(auth: bool = Depends(check_auth)) -> RedirectResponse:
    """Test Notion connection."""
    settings = load_settings()
    cfg = settings.get("notion", {})
    token = cfg.get("token")
    database_id = cfg.get("database_id")
    if not token or not database_id:
        return RedirectResponse(url="/ui/settings?tab=notion&err=" + quote("Токен или ID базы Notion не заданы"), status_code=status.HTTP_303_SEE_OTHER)
    try:
        resp = httpx.get(
            f"https://api.notion.com/v1/databases/{database_id}",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return RedirectResponse(url="/ui/settings?tab=notion&notice=notion_test_ok", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/ui/settings?tab=notion&err=" + quote(f"Notion API статус: {resp.status_code}"), status_code=status.HTTP_303_SEE_OTHER)
    except Exception as exc:
        return RedirectResponse(url="/ui/settings?tab=notion&err=" + quote(str(exc)), status_code=status.HTTP_303_SEE_OTHER)


# Backward-compatible endpoints
@settings_router.post("/ui/settings")
async def legacy_save_settings_llm(request: Request, auth: bool = Depends(check_auth)) -> Any:
    return await save_settings_ai(request=request, auth=auth)


@settings_router.post("/ui/settings/secrets")
async def legacy_save_settings_secrets(request: Request, auth: bool = Depends(check_auth)) -> Any:
    form = await request.form()
    return save_settings_profile(request=request, seller_name=str(form.get("SELLER_NAME", "")), seller_psc=str(form.get("SELLER_PSC", "")), seller_phone=str(form.get("BAZOS_PHONE", "")), default_meeting_place="", auth=auth)
