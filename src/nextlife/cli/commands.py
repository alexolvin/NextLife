"""Command handlers for NextLife CLI subcommands (item 3: thin HTTP client).

Every handler talks to the NextLife API on NEXTLIFE_BASE_URL (loopback
default, see http_client.DEFAULT_BASE_URL) authenticated with
NEXTLIFE_OPENCLAW_TOKEN. No database access, no config loading: the server
resolves marketplaces, categories and data paths.
"""

import json
import sys
from pathlib import Path
from typing import Any

from nextlife.cli import http_client
from nextlife.core.exceptions import InputError


def _get(flags: dict[str, Any], key: str, required: bool = True) -> Any:
    value = flags.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError(f"Missing required --{key.replace('_', '-')} argument")
        return None
    return value


def _call(token: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    _, body = http_client.request(method, path, token=token, **kwargs)
    return body


def handle_status_cmd(token: str) -> dict[str, Any]:
    return _call(token, "GET", "/api/oc/status")


def handle_item_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else ""
    if subcmd == "create":
        title = str(_get(flags, "title"))
        category = str(_get(flags, "category"))
        price = _get(flags, "price", required=False)
        currency = _get(flags, "currency", required=False)
        condition = str(_get(flags, "condition", required=False) or "used")
        body: dict[str, Any] = {"title": title, "category": category, "condition": condition}
        if price is not None:
            body["price"] = price
        if currency:
            body["currency"] = str(currency)
        return _call(token, "POST", "/api/oc/items", json_body=body)
    if subcmd == "show":
        code = str(_get(flags, "code"))
        return _call(token, "GET", f"/api/oc/items/{code}")
    raise InputError(f"Unknown item subcommand: {subcmd}")


def handle_research_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else "start"
    code = str(_get(flags, "code"))
    if subcmd == "start":
        query = _get(flags, "query", required=False)
        path = f"/api/oc/items/{code}/research" + (f"?query={query}" if query else "")
        return _call(token, "POST", path)
    if subcmd == "report":
        return _call(token, "GET", f"/api/oc/items/{code}/research/report")
    raise InputError(f"Unknown research subcommand: {subcmd}")


def handle_media_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else "ingest"
    code = str(_get(flags, "code"))
    if subcmd == "ingest":
        raw_files = _get(flags, "files")
        file_list = [str(raw_files)] if isinstance(raw_files, str) else [str(p) for p in raw_files]
        missing = [p for p in file_list if not Path(p).exists()]
        if missing:
            raise InputError(f"File not found: {', '.join(missing)}")
        role = str(_get(flags, "role", required=False) or "ORIGINAL")
        ai_raw = flags.get("ai_generated", flags.get("ai", False))
        ai = ai_raw is True or str(ai_raw).strip().lower() in ("1", "true", "yes")
        vlm_note = str(_get(flags, "vlm_verdict", required=False) or "")
        opened = [open(p, "rb") for p in file_list]
        files = [("files", (Path(p).name, fh)) for p, fh in zip(file_list, opened, strict=True)]
        form = {"role": role.upper(), "ai_generated": "true" if ai else "false", "vlm_verdict": vlm_note}
        try:
            return _call(token, "POST", f"/api/oc/items/{code}/photos", files=files, form=form)
        finally:
            for fh in opened:
                fh.close()
    if subcmd == "build":
        return _call(token, "POST", f"/api/oc/items/{code}/media-build")
    raise InputError(f"Unknown media subcommand: {subcmd}")


def handle_draft_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else "build"
    code = str(_get(flags, "code"))
    if subcmd == "build":
        return _call(token, "POST", f"/api/oc/items/{code}/draft")
    raise InputError(
        f"Unknown draft subcommand: {subcmd}. "
        "Approval is an owner action in the web UI (/ui/draft/{code}); the CLI only builds drafts."
    )


def handle_publish_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else ""
    code = str(_get(flags, "code"))
    if subcmd == "status":
        return _call(token, "GET", f"/api/oc/items/{code}/listings")
    dry_run = bool(flags.get("dry_run", False))
    return _call(token, "POST", f"/api/oc/items/{code}/publish", json_body={"dry_run": dry_run})


def handle_outbox_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else "pull"
    if subcmd == "pull":
        return _call(token, "GET", "/api/oc/outbox")
    if subcmd == "ack":
        outbox_id = str(_get(flags, "id"))
        raw_evidence = _get(flags, "delivery_evidence")
        try:
            delivery_evidence = json.loads(str(raw_evidence)) if isinstance(raw_evidence, str) else raw_evidence
        except Exception as exc:
            raise InputError(f"Invalid delivery evidence JSON: {exc}") from exc
        if not isinstance(delivery_evidence, dict) or not delivery_evidence:
            raise InputError("delivery-evidence must be a non-empty JSON object")
        return _call(token, "POST", f"/api/oc/outbox/{outbox_id}/ack", json_body={"delivery_evidence": delivery_evidence})
    raise InputError(f"Unknown outbox subcommand: {subcmd}")


def handle_inbox_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else "pending"
    if subcmd == "push":
        try:
            payload = json.loads(sys.stdin.read())
        except Exception as exc:
            raise InputError(f"Invalid JSON on stdin for inbox push: {exc}") from exc
        if not isinstance(payload, dict):
            raise InputError("inbox push payload must be a JSON object")
        return _call(token, "POST", "/api/oc/inbox", json_body=payload)
    if subcmd == "pending":
        return _call(token, "GET", "/api/oc/inbox/pending")
    raise InputError(f"Unknown inbox subcommand: {subcmd}")


def handle_deal_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    subcmd = positionals[1] if len(positionals) > 1 else "close"
    code = str(_get(flags, "code"))
    if subcmd == "close":
        return _call(token, "POST", "/api/oc/deals/close", json_body={"code": code})
    raise InputError(f"Unknown deal subcommand: {subcmd}")


def handle_canaries_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    """Daily channel canaries: run live probes / show channel state."""
    subcmd = positionals[1] if len(positionals) > 1 else "run"
    if subcmd == "run":
        key = _get(flags, "key", required=False)
        body: dict[str, Any] = {}
        if key:
            body["key"] = str(key)
        return _call(token, "POST", "/api/oc/canaries/run", json_body=body)
    if subcmd == "status":
        return _call(token, "GET", "/api/oc/canaries")
    raise InputError(f"Unknown canaries subcommand: {subcmd}")


def handle_search_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    """Search a marketplace app via the phone broker."""
    platform = str(_get(flags, "platform"))
    query = str(_get(flags, "query"))
    limit = int(_get(flags, "limit", required=False) or 5)
    return _call(token, "POST", "/api/oc/search", json_body={"platform": platform, "query": query, "limit": limit})


def _parse_set_flags(flags: dict[str, Any]) -> dict[str, str]:
    """Parse repeated ``--set field=value`` pairs into a dict."""
    raw = flags.get("set")
    if raw is None or raw is True:
        return {}
    pairs = raw if isinstance(raw, list) else [raw]
    result: dict[str, str] = {}
    for pair in pairs:
        if "=" not in str(pair):
            raise InputError(f"Invalid --set value (expected field=value): {pair}")
        key, value = str(pair).split("=", 1)
        result[key.strip()] = value
    return result


def handle_stock_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
    """Склад (Warehouse) gateway — ТЗ «Склад» §5.3. Thin HTTP client for /api/oc/stock."""
    subcmd = positionals[1] if len(positionals) > 1 else "list"
    if subcmd == "list":
        group = str(_get(flags, "group", required=False) or "all")
        q = _get(flags, "q", required=False)
        path = f"/api/oc/stock?group={group}" + (f"&q={q}" if q else "")
        return _call(token, "GET", path)
    if subcmd == "show":
        code = str(_get(flags, "code"))
        return _call(token, "GET", f"/api/oc/stock/{code}")
    if subcmd == "create":
        title = str(_get(flags, "title"))
        body: dict[str, Any] = {"title": title}
        body.update(_parse_set_flags(flags))
        return _call(token, "POST", "/api/oc/stock", json_body=body)
    if subcmd == "update":
        code = str(_get(flags, "code"))
        fields = _parse_set_flags(flags)
        if not fields:
            raise InputError("stock update requires at least one --set field=value")
        return _call(token, "PATCH", f"/api/oc/stock/{code}", json_body=fields)
    if subcmd == "sold":
        code = str(_get(flags, "code"))
        return _call(token, "POST", f"/api/oc/stock/{code}/sold")
    if subcmd == "delete":
        code = str(_get(flags, "code"))
        params: list[str] = []
        if flags.get("confirm"):
            params.append("confirm=true")
        if flags.get("confirm_listings"):
            params.append("confirm_listings=true")
        path = f"/api/oc/stock/{code}" + (f"?{'+'.join(params)}" if params else "")
        return _call(token, "DELETE", path)
    if subcmd == "photos":
        code = str(_get(flags, "code"))
        return _call(token, "GET", f"/api/oc/stock/{code}/photos")
    if subcmd == "photo-delete":
        code = str(_get(flags, "code"))
        photo_id = str(_get(flags, "photo_id"))
        return _call(token, "DELETE", f"/api/oc/stock/{code}/photos/{photo_id}")
    raise InputError(f"Unknown stock subcommand: {subcmd}")
