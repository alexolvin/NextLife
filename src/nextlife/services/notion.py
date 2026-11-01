"""Notion calendar integration service (TZ §7.4).

Exports shipment and meeting schedule events to Notion calendar database.
Protocol: POST/PATCH https://api.notion.com/v1/pages with header Notion-Version: 2022-06-28.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from nextlife.core.config import get_data_dir
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.storage.models import Deal, Item, Lot

log = logging.getLogger("nextlife.notion")
NOTION_API_VERSION = "2022-06-28"


class NotionIntegrationError(NextLifeError):
    """Raised when Notion API call fails or configuration is missing."""


def _settings_path() -> Path:
    return get_data_dir() / "settings.json"


def get_notion_config() -> dict[str, str]:
    """Return Notion settings (token, database_id, title_prop, date_prop)."""
    path = _settings_path()
    cfg: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = data.get("notion", {})
        except Exception:
            cfg = {}

    return {
        "token": str(cfg.get("token") or os.environ.get("NEXTLIFE_NOTION_TOKEN") or "").strip(),
        "database_id": str(cfg.get("database_id") or os.environ.get("NEXTLIFE_NOTION_DATABASE_ID") or "").strip(),
        "title_prop": str(cfg.get("title_prop") or "Name").strip(),
        "date_prop": str(cfg.get("date_prop") or "Date").strip(),
    }


def verify_notion_connection(config: dict[str, str] | None = None) -> bool:
    """Verify access to Notion database via GET /v1/databases/{id}."""
    cfg = config or get_notion_config()
    token = cfg.get("token")
    db_id = cfg.get("database_id")
    if not token or not db_id:
        return False

    url = f"https://api.notion.com/v1/databases/{db_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
    }
    try:
        res = httpx.get(url, headers=headers, timeout=15.0)
        return res.status_code == 200
    except Exception as exc:
        log.warning("Notion verification failed: %s", exc)
        return False


def create_or_update_schedule_event(
    session: Session,
    deal_id: str,
    event_type: str,  # 'shipment' or 'meeting'
    dt_iso: str,
    location_or_carrier: str,
    note: str = "",
) -> dict[str, Any]:
    """Create or update Notion calendar event page for a deal (§7.4)."""
    cfg = get_notion_config()
    token = cfg.get("token")
    db_id = cfg.get("database_id")
    title_prop = cfg.get("title_prop") or "Name"
    date_prop = cfg.get("date_prop") or "Date"

    if not token or not db_id:
        raise NotionIntegrationError("Notion не настроен: укажите токен и ID базы данных в Настройках.")

    deal = session.get(Deal, deal_id)
    if not deal:
        raise NotionIntegrationError(f"Сделка {deal_id} не найдена")

    item = None
    lot = session.get(Lot, deal.lot_id) if deal.lot_id else None
    if lot and lot.item_id:
        item = session.get(Item, lot.item_id)

    code = item.code if item else deal_id[:8]
    title_text = item.title if item else "Предмет сделки"
    prefix = "Отправка" if event_type == "shipment" else "Встреча"
    event_title = f"{prefix} #{code} {title_text}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }

    properties: dict[str, Any] = {
        title_prop: {
            "title": [{"text": {"content": event_title}}]
        },
        date_prop: {
            "date": {"start": dt_iso}
        },
    }

    evidence = dict(deal.closure_evidence or {})
    page_id = evidence.get("notion_page_id")

    if page_id:
        # Update existing Notion page
        url = f"https://api.notion.com/v1/pages/{page_id}"
        payload = {"properties": properties}
        try:
            res = httpx.patch(url, headers=headers, json=payload, timeout=20.0)
            if res.status_code != 200:
                raise NotionIntegrationError(f"Notion вернул ошибку: {res.text[:200]}")
        except Exception as exc:
            raise NotionIntegrationError(f"Ошибка обновления страницы в Notion: {exc}") from exc
    else:
        # Create new Notion page
        url = "https://api.notion.com/v1/pages"
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": f"📍 Место / Перевозчик: {location_or_carrier}\nЗаметка: {note}"}
                        }
                    ]
                }
            }
        ]
        payload = {
            "parent": {"database_id": db_id},
            "properties": properties,
            "children": children,
        }
        try:
            res = httpx.post(url, headers=headers, json=payload, timeout=20.0)
            if res.status_code not in (200, 201):
                raise NotionIntegrationError(f"Notion вернул ошибку {res.status_code}: {res.text[:200]}")
            page_data = res.json()
            page_id = page_data.get("id")
            evidence["notion_page_id"] = page_id
            deal.closure_evidence = evidence
            session.flush()
        except Exception as exc:
            raise NotionIntegrationError(f"Ошибка создания страницы в Notion: {exc}") from exc

    record_audit_event(
        session=session,
        actor="USER",
        action="NOTION_EVENT_SCHEDULED",
        entity="DEAL",
        entity_id=deal.id,
        data={"notion_page_id": page_id, "event_type": event_type, "dt": dt_iso},
    )

    return {
        "status": "ok",
        "page_id": page_id,
        "event_title": event_title,
        "dt": dt_iso,
    }
