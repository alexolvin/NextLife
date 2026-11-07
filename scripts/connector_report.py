#!/usr/bin/env python3
"""Build the connector report from the evidence registry and the database.

The script does not invent a publication status. A platform with no
result.json is NOT_STARTED. Wording of success is not used.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
REPORTS = ROOT / "reports"

CLOSED = {
    "NOT_STARTED",
    "FAILED",
    "SUBMITTED_UNVERIFIED",
    "PENDING_MODERATION",
    "LIVE_VERIFIED",
    "PENDING_OWNER_CHECK",
    "ACCEPTED_BY_OWNER",
    "BLOCKED",
    "NOT_ALLOWED",
}

PLATFORMS = [
    "bazos_cz",
    "bazos_sk",
    "sbazar_cz",
    "bazar_cz",
    "aukro_cz",
    "vinted",
    "facebook_marketplace",
    "mimibazar_cz",
    "trhknih_cz",
    "kleinanzeigen_de",
    "willhaben_at",
    "olx_pl",
]


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_results() -> dict[str, dict[str, Any]]:
    found: dict[str, tuple[float, dict[str, Any]]] = {}
    if not EVIDENCE.is_dir():
        return {}
    for path in EVIDENCE.glob("*/*/result.json"):
        platform = path.relative_to(EVIDENCE).parts[0]
        if platform in {"stage0", "search"}:
            continue
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        status = str(data.get("status") or "")
        if status not in CLOSED:
            data = dict(data)
            data["status"] = "NOT_STARTED"
            data["owner_action"] = data.get("owner_action") or "статус в реестре вне закрытого словаря"
        stamp = path.stat().st_mtime
        prev = found.get(platform)
        if prev is None or stamp >= prev[0]:
            data = dict(data)
            data["evidence_dir"] = str(path.parent)
            found[platform] = (stamp, data)
    return {key: value for key, (_, value) in found.items()}


def _stage0_dirs() -> list[Path]:
    if not EVIDENCE.is_dir():
        return []
    root = EVIDENCE / "stage0"
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _rows(name: str, *, latest: bool = False) -> list[Any]:
    """Stage-0 lists. Facts use the newest run; incidents stay append-only."""
    dirs = _stage0_dirs()
    if latest and dirs:
        dirs = dirs[-1:]
    rows: list[Any] = []
    for directory in dirs:
        data = _load_json(directory / name)
        if isinstance(data, list):
            rows.extend(data)
    return rows


def _search_rows() -> list[dict[str, Any]]:
    """Newest search result per platform. Older files stay in the registry."""
    found: dict[str, tuple[float, dict[str, Any]]] = {}
    if not EVIDENCE.is_dir():
        return []
    for path in EVIDENCE.glob("search/*/*/result.json"):
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        platform = str(data.get("platform") or path.parent.parent.name)
        stamp = path.stat().st_mtime
        prev = found.get(platform)
        if prev is None or stamp >= prev[0]:
            found[platform] = (stamp, data)
    return [row for _, row in found.values()]


def _db_rows() -> list[dict[str, str]]:
    try:
        from nextlife.storage.db import get_db_session
        from nextlife.storage.models import Listing
        from sqlalchemy import select
    except Exception:
        return []
    out = []
    with get_db_session() as session:
        listings = session.scalars(select(Listing).where(Listing.item_code == "130")).all()
        for row in listings:
            out.append({
                "marketplace": row.marketplace_key,
                "status": row.status,
                "external_id": row.external_id or "",
                "external_url": row.external_url or "",
            })
    return out


def render(comment: str = "") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results = latest_results()
    incidents = _rows("incidents.json")
    claims = _rows("claims.json", latest=True)
    changes = _rows("criteria_changes.json", latest=True)
    config_changes = _rows("config_changes.json", latest=True)
    unchecked = _rows("not_checked.json", latest=True)
    owner = _rows("owner_actions.json", latest=True)
    searches = _search_rows()
    db_rows = _db_rows()

    lines = [
        "# Отчёт коннекторов",
        "",
        f"Собран: {now}. Источник статусов — реестр evidence и база NextLife.",
        "",
        "## 1. Инциденты и дефекты",
        "",
    ]
    if not incidents:
        lines.append("Записей инцидентов в реестре нет.")
    seen_incidents: set[str] = set()
    for row in incidents:
        text = str(row.get("text", row))
        if not text or text in seen_incidents:
            continue
        seen_incidents.add(text)
        lines.append(f"- {text}")
    lines += ["", "## 2. Сводная таблица", "",
              "| Площадка | Статус | Публичный URL | Внешний ID | Проверка | Фото | Доказательства | Нужно от владельца |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for platform in PLATFORMS:
        row = results.get(platform, {})
        status = row.get("status") or "NOT_STARTED"
        if platform == "facebook_marketplace" and status == "NOT_STARTED":
            pack = list(EVIDENCE.glob("facebook_marketplace/pack/*.zip")) if EVIDENCE.is_dir() else []
            with_photos = []
            for item in pack:
                try:
                    from zipfile import ZipFile
                    with ZipFile(item) as archive:
                        if any(name.startswith("photos/") for name in archive.namelist()):
                            with_photos.append(item)
                except OSError:
                    continue
            if with_photos:
                status = "MANUAL"
                row = {
                    "owner_action": "Опубликовать пакет вручную и нажать «Подтверждаю»",
                    "evidence_dir": str(with_photos[-1].parent),
                }
        url = row.get("public_url") or ""
        ext = row.get("external_id") or ""
        checked = row.get("checked_at") or ""
        photos = ""
        if row.get("photos_found"):
            photos = f"{row.get('photos_matched', 0)} из {row.get('photos_found')}"
        lines.append(
            f"| {platform} | {status} | {url or '—'} | {ext or '—'} | {checked or '—'} | {photos or '—'} | {row.get('evidence_dir') or '—'} | {row.get('owner_action') or '—'} |"
        )
    lines += ["", "Записи лота #130 в базе:", ""]
    if not db_rows:
        lines.append("Записей нет либо база недоступна этому запуску.")
    for row in db_rows:
        lines.append(
            f"- {row['marketplace']}: статус базы {row['status']}, URL {row['external_url'] or '—'}, ID {row['external_id'] or '—'}"
        )
    lines += ["", "## 3. Проверка поиска", ""]
    if not searches:
        lines.append("Проверок поиска в реестре нет.")
    for row in searches:
        lines.append(f"### {row.get('platform', '')}")
        for link in row.get("links") or []:
            lines.append(
                "- {url} — HTTP {status}, заголовок: {title}, совпадение с запросом: {match}".format(
                    url=link.get("url", ""),
                    status=link.get("http_status", ""),
                    title=link.get("page_title") or link.get("connector_title") or "—",
                    match=link.get("matches_query", ""),
                )
            )
        if row.get("error"):
            lines.append(f"- ошибка сбора: {row['error']}")
    lines += ["", "## 4. Факты Этапа 0", "",
              "| Утверждение | Отметка | Доказательство |",
              "| --- | --- | --- |"]
    if not claims:
        lines.append("| — | не проверено | реестр пуст |")
    for row in claims:
        lines.append(f"| {row.get('claim', '')} | {row.get('verdict', '')} | {row.get('evidence', '')} |")
    lines += ["", "## 5. Изменения критериев", ""]
    if not changes:
        lines.append("Изменений критериев нет.")
    for row in changes:
        lines.append(f"- Было: {row.get('before', '')}")
        lines.append(f"  Стало: {row.get('after', '')}")
        lines.append(f"  Почему: {row.get('why', '')}")
    lines += ["", "## 6. Изменения конфигурации", ""]
    if not config_changes:
        lines.append("Записей нет.")
    for row in config_changes:
        lines.append(f"- {row.get('text', row)}")
    lines += ["", "## 7. Что не проверено", ""]
    if not unchecked:
        lines.append("Список пуст.")
    for row in unchecked:
        lines.append(f"- {row.get('text', row)}")
    lines += ["", "## 8. Сообщение владельцу (ТЗ v2 §8.3)", ""]
    owner_rendered = False
    try:
        from nextlife.storage.db import get_db_session
        from nextlife.services.connectors_map import compute_connectors_map, format_owner_message
        with get_db_session() as session:
            map_statuses = compute_connectors_map(session)
            msg = format_owner_message(map_statuses)
            lines.append(msg)
            owner_rendered = True
    except Exception:
        owner_rendered = False

    if not owner_rendered:
        actions = list(owner)
        for platform in PLATFORMS:
            action = (results.get(platform) or {}).get("owner_action")
            if action:
                actions.append({"text": f"{platform}: {action}"})
        if not actions:
            lines.append("От владельца ничего не запрошено.")
        seen = set()
        for row in actions:
            text = row.get("text", "")
            if text and text not in seen:
                seen.add(text)
                lines.append(f"- {text}")
    lines += ["", "## 9. Комментарий агента", "", comment.strip() or "Комментарий не приложен.", ""]
    return "\n".join(lines) + "\n"


def short_version(full: str) -> str:
    parts = []
    capture = False
    for line in full.splitlines():
        if line.startswith("## 2."):
            capture = True
        elif line.startswith("## 3."):
            capture = False
        elif line.startswith("## 8."):
            capture = True
        elif line.startswith("## 9."):
            capture = False
        if capture:
            parts.append(line)
    return "\n".join(parts).strip() + "\n"


def main() -> None:
    comment_path = EVIDENCE / "agent_comment.md"
    comment = comment_path.read_text(encoding="utf-8") if comment_path.is_file() else ""
    full = render(comment)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "final.md").write_text(full, encoding="utf-8")
    (REPORTS / "final-short.md").write_text(short_version(full), encoding="utf-8")
    stage0 = render(comment)
    (REPORTS / "stage0.md").write_text(stage0, encoding="utf-8")
    mirror = ROOT / ".a" / "reports"
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "final.md").write_text(full, encoding="utf-8")
    (mirror / "stage0.md").write_text(stage0, encoding="utf-8")
    print(REPORTS / "final.md")


if __name__ == "__main__":
    main()
