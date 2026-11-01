"""FastAPI application for NextLife."""

import re
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from nextlife.api.routes import TEMPLATES_DIR, router
from nextlife.core.config import get_database_url, get_media_dir, load_config
from nextlife.obs.logger import setup_logger

logger = setup_logger("nextlife.api")

# N4-04: код лота/предмета в URL (для страницы ошибки)
LOT_CODE_RE = re.compile(r"/ui/(?:items|lots|draft|wizard)/([A-Za-z0-9]{2,})")
# Страница ошибки — статический шаблон (без HTML в src/, DoD test_zero_html_tags_in_src)
ERROR_PAGE_PATH = TEMPLATES_DIR / "error.html"


def _render_error_page(error_id: str, lot_line: str) -> str | None:
    """Статический шаблон с текстовой подстановкой (не зависит от Jinja:
    страница обязана отработать, даже если сбой в рендеринге шаблонов)."""
    try:
        tpl = ERROR_PAGE_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    return tpl.replace("__ERROR_ID__", error_id).replace("__LOT_LINE__", lot_line)


def create_app() -> FastAPI:
    config = load_config()
    # Deployment fail-fast (item 0.3 / C-01): the DSN must be resolvable before
    # the server starts serving. get_database_url() resolves the SecretRef
    # (NEXTLIFE_DSN) from the process env; if the var is missing it raises a
    # SecretResolutionError that names it. This makes a botched deploy (env var
    # not exported) abort at startup instead of dying on the first DB request.
    # It only resolves the secret — it does NOT open a connection.
    get_database_url(config)
    app = FastAPI(title="NextLife API", version="0.1.0")

    media_dir = get_media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def auth_cookie_middleware(request: Request, call_next: Any) -> Any:
        token = request.query_params.get("token")
        if token and request.method == "GET" and not request.url.path.startswith("/media"):
            from urllib.parse import quote, urlencode
            from fastapi.responses import RedirectResponse
            from nextlife.core.secrets import resolve_secret
            expected = resolve_secret(config.app.auth.web_token_secret_ref)
            clean_query = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            clean_qs = urlencode(clean_query)
            if token.strip() == expected:
                target_path = "/ui/items" if request.url.path in ("/", "/ui") else request.url.path
                target_url = target_path + (f"?{clean_qs}" if clean_qs else "")
                response = RedirectResponse(url=target_url, status_code=302)
                response.set_cookie(key="nextlife_token", value=token, httponly=True, samesite="lax")
                return response
            else:
                err = quote("Неверный токен безопасности. Пожалуйста, проверьте ввод.")
                return RedirectResponse(url=f"/ui/login?error={err}", status_code=302)

        response = await call_next(request)
        if token:
            response.set_cookie(key="nextlife_token", value=token, httponly=True, samesite="lax")
        return response

    @app.exception_handler(401)
    async def unauthorized_exception_handler(request: Request, exc: Any) -> Any:
        accept = request.headers.get("accept", "")
        if request.url.path.startswith("/ui") and "text/html" in accept:
            from fastapi.responses import RedirectResponse
            next_p = "" if request.url.path in ("/", "/ui", "/ui/items") else f"?next={request.url.path}"
            return RedirectResponse(url=f"/ui/login{next_p}", status_code=303)
        from fastapi.responses import JSONResponse
        detail = getattr(exc, "detail", "Unauthorized: invalid or missing security token.")
        return JSONResponse(status_code=401, content={"detail": detail})

    # N4-04: необработанное исключение -> HTTP 500 + страница ошибки
    # (для /ui) с кодом лота и идентификатором ошибки; исключение пишется
    # в лог с тем же идентификатором. Ошибка НЕ скрывается: статус 500.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> Any:
        error_id = uuid.uuid4().hex[:12]
        logger.error(
            f"[{error_id}] Unhandled exception on {request.method} {request.url.path}: "
            f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
        )
        m = LOT_CODE_RE.search(request.url.path)
        lot_code = m.group(1) if m else None
        accept = request.headers.get("accept", "")
        if request.url.path.startswith("/ui") and "text/html" in accept:
            from fastapi.responses import HTMLResponse, PlainTextResponse
            lot_line = f"Затрагиваемый лот: <code>{lot_code}</code>.<br>" if lot_code else ""
            html = _render_error_page(error_id, lot_line)
            if html is not None:
                return HTMLResponse(content=html, status_code=500)
            # Шаблон недоступен (сломанный деплой) — голый 500 с текстом,
            # чтобы ошибка не была проглочена молча.
            return PlainTextResponse(f"NextLife: internal error {error_id}", status_code=500)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error", "error_id": error_id, "lot_code": lot_code},
        )

    @app.get("/")
    @app.get("/ui")
    def root_redirect(request: Request) -> Any:
        from fastapi.responses import RedirectResponse
        from nextlife.core.secrets import resolve_secret
        token = request.cookies.get("nextlife_token")
        expected = resolve_secret(config.app.auth.web_token_secret_ref)
        if token and token.strip() == expected:
            return RedirectResponse(url="/ui/items", status_code=307)
        q = str(request.url.query)
        return RedirectResponse(url=f"/ui/login{('?' + q) if q else ''}", status_code=307)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Deploy-integrity probe: commit + built_at from build-info.json.

        Written by scripts/install.sh at deploy time. Absent/corrupt file →
        "unknown" (never 500). V0.1: commit must match `git rev-parse HEAD`.
        """
        import json as _json

        from nextlife.core.config import get_data_dir

        info: dict[str, str] = {"commit": "unknown", "built_at": "unknown"}
        info_path = get_data_dir() / "build-info.json"
        if info_path.exists():
            try:
                loaded = _json.loads(info_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    info = {
                        "commit": str(loaded.get("commit", "unknown")),
                        "built_at": str(loaded.get("built_at", "unknown")),
                    }
            except (OSError, ValueError):
                pass
        return info

    @app.get("/status")
    def status() -> dict[str, Any]:
        return {
            "status": "running",
            "base_currency": config.app.base_currency,
            "marketplaces_count": len(config.marketplaces),
            "android_bridge_enabled": config.app.android_bridge.enabled,
        }

    from nextlife.api.routes_dashboard import dashboard_router
    from nextlife.api.routes_deal_screen import deal_screen_router
    from nextlife.api.routes_evidence import evidence_router
    from nextlife.api.routes_lots import lots_router
    from nextlife.api.routes_openclaw import oc_router
    from nextlife.api.routes_openclaw_ops import oc_ops_router
    from nextlife.api.routes_openclaw_stock import oc_stock_router
    from nextlife.api.routes_jobs import router as jobs_router
    from nextlife.api.routes_photos import photos_router
    from nextlife.api.routes_preview import preview_router
    from nextlife.api.routes_publish import publish_router
    from nextlife.api.routes_research import research_router
    from nextlife.api.routes_shipping import shipping_router
    from nextlife.api.routes_stock import stock_router
    from nextlife.api.routes_threads import threads_router
    from nextlife.api.routes_search import router as search_router
    from nextlife.api.routes_listings import router as listings_router
    from nextlife.api.routes_buy import router as buy_router
    from nextlife.api.routes_messages import messages_router
    from nextlife.api.routes_settings import settings_router
    from nextlife.api.routes_openclaw_v2 import oc_v2_router
    from nextlife.api.routes_bazos_recon import bazos_recon_router

    app.include_router(messages_router)
    app.include_router(threads_router)
    app.include_router(research_router)
    app.include_router(settings_router)
    app.include_router(bazos_recon_router)
    app.include_router(router)
    app.include_router(stock_router)
    app.include_router(lots_router)
    app.include_router(photos_router)
    app.include_router(preview_router)
    app.include_router(publish_router)
    app.include_router(dashboard_router)
    app.include_router(shipping_router)
    app.include_router(evidence_router)
    app.include_router(deal_screen_router)
    app.include_router(oc_router)
    app.include_router(oc_ops_router)
    app.include_router(oc_stock_router)
    app.include_router(oc_v2_router)
    app.include_router(jobs_router)
    app.include_router(search_router)
    app.include_router(listings_router)
    app.include_router(buy_router)
    return app


def main() -> None:
    try:
        config = load_config()
    except Exception as exc:
        logger.error(f"Configuration failure: {exc}")
        sys.exit(1)

    app = create_app()
    logger.info("Starting NextLife API server...")
    uvicorn.run(
        app,
        host=config.app.server.host,
        port=config.app.server.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
