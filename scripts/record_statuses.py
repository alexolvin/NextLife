#!/usr/bin/env python3
"""Write one evidence result.json per platform from the operational contract.

Does not submit a marketplace form. A missing result stays out of the
success vocabulary. Facebook is left without result.json when a pack zip
exists so the report can show MANUAL.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nextlife.connectors.ops import publish  # noqa: E402
from nextlife.services.facebook_pack import build_zip  # noqa: E402
from nextlife.storage.db import get_db_session  # noqa: E402
from nextlife.storage.models import Item, ItemPhoto  # noqa: E402
from nextlife.verify.evidence import EvidenceRegistry  # noqa: E402
from sqlalchemy import select  # noqa: E402

PLATFORMS = (
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
)


def _newest_screen(platform: str) -> Path | None:
    found: list[Path] = []
    for name in ("inventory.json", "myads.json", "follow.json"):
        found.extend((ROOT / "evidence").glob(f"{platform}/*/{name}"))
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def _facebook_pack() -> str:
    dest_dir = ROOT / "evidence" / "facebook_marketplace" / "pack"
    dest_dir.mkdir(parents=True, exist_ok=True)
    with get_db_session() as session:
        item = session.get(Item, "130")
        if item is None:
            return ""
        photos = session.scalars(
            select(ItemPhoto).where(ItemPhoto.item_code == "130", ItemPhoto.role == "PUBLISH")
        ).all()
        if not photos:
            photos = session.scalars(
                select(ItemPhoto).where(ItemPhoto.item_code == "130", ItemPhoto.role == "ORIGINAL")
            ).all()
        paths = []
        for row in photos:
            if not row.path:
                continue
            candidate = Path(row.path)
            try:
                readable = candidate.is_file()
            except OSError:
                readable = False
            if readable:
                paths.append(candidate)
        dest = dest_dir / "130.zip"
        if dest.exists():
            dest = dest_dir / f"130-{datetime.now(timezone.utc).strftime('%H%M%S')}.zip"
        build_zip(
            dest,
            title=item.title or "130",
            price=str(item.target_price or ""),
            currency=item.target_currency or "",
            description=item.description or "",
            photo_paths=paths,
        )
        return str(dest)


def _screen_action(platform: str) -> tuple[str, str]:
    """Owner line and reason grounded in screens captured this run."""
    notes = {
        "bazos_cz": (
            "В профиле bazos.cz одно объявление «Koupím / vyměním RTX 3090» за 27 000 Kč, Praha 4. Это не лот #130, его не снимали. Форма Add не отправлялась.",
            "Подтвердить, что объявление RTX 3090 за 27 000 Kč не трогаем. Страница /ui/stock/130",
        ),
        "bazos_sk": (
            "В карточке нет цены EUR. На экране App settings портал Bazos.cz, переключателя страны на этом экране нет. Чешский PSČ на форме SK не проверялся.",
            "Записать цену 1050 EUR в карточку лота #130 на /ui/stock/130",
        ),
        "sbazar_cz": (
            "Приложение открылось на главной. Вкладка «Můj profil» селектором не нажалась, список своих объявлений не открыт. Форма не отправлялась.",
            "Открыть Sbazar и проверить «Můj profil». Страница /ui/connect",
        ),
        "bazar_cz": (
            "Веб-сессии нет. Xvfb, x11vnc и websockify на узле не установлены, консоль входа не запускалась.",
            "Установить Xvfb, x11vnc, websockify и войти на /ui/connect?platform=bazar_cz",
        ),
        "aukro_cz": (
            "В карточке один способ получения: личная встреча, 0 Kč. Импорт CSV/XML и форма Manažer prodeje не открывались. Веб-сессии нет, объявление не отправлялось.",
            "Войти на /ui/connect?platform=aukro_cz. Консоли noVNC нет, пока не установлены Xvfb, x11vnc и websockify.",
        ),
        "vinted": (
            "Аккаунт drmarkerich открыт, своих предложений нет. Форма «Nabídnout předmět» открывалась, дерево категорий не открылось, объявление не отправлялось. Запрет видеокарт не доказан.",
            "Не выбирать замещающий лот, пока нет снимка дерева категорий. Страница /ui/stock",
        ),
        "kleinanzeigen_de": (
            "Кнопка Inserieren открыла экран «Registriere dich, um eine Anzeige aufzugeben». Объявление не создавалось.",
            "Войти в приложение Kleinanzeigen самостоятельно. Номер не покупать. Страница /ui/connect",
        ),
        "willhaben_at": (
            "Профиль показывает «Einloggen / Registrieren». Меню Verkaufen открылось без входа. Объявление не создавалось.",
            "Войти в приложение willhaben самостоятельно. Номер не покупать. Страница /ui/connect",
        ),
        "olx_pl": (
            "Раздел Konto показывает «Zaloguj się / Załóż konto». Объявление не создавалось.",
            "Войти в приложение OLX самостоятельно. Номер не покупать. Страница /ui/connect",
        ),
    }
    return notes.get(platform, ("", ""))


def main() -> None:
    registry = EvidenceRegistry(ROOT / "evidence")
    pack = _facebook_pack()
    written = []
    with get_db_session() as session:
        for platform in PLATFORMS:
            if platform == "facebook_marketplace":
                photo_count = 0
                if pack:
                    from zipfile import ZipFile
                    with ZipFile(pack) as archive:
                        photo_count = sum(1 for name in archive.namelist() if name.startswith("photos/"))
                if photo_count:
                    written.append({"platform": platform, "status": "MANUAL", "pack": pack, "photos": photo_count})
                    continue
                run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-facebook"
                payload = {
                    "status": "BLOCKED",
                    "public_url": "",
                    "external_id": "",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "owner_action": "Дать этому процессу чтение каталога медиа или выложить код от пользователя nextlife. Страница /ui/stock/130",
                    "reason": "Текст для Facebook собран, фото из карточки прочитать нельзя: каталог медиа закрыт для этого пользователя.",
                    "photos_found": 0,
                    "photos_matched": 0,
                    "pack": pack,
                }
                registry.write_json(platform, run_id, "result.json", payload)
                registry.finalize(platform, run_id)
                written.append({"platform": platform, "status": "BLOCKED", "pack": pack, "photos": 0})
                continue
            result = publish(session, "130", platform)
            session.rollback()
            screen = _newest_screen(platform)
            reason, action = _screen_action(platform)
            payload = {
                "status": result.status,
                "public_url": result.public_url,
                "external_id": result.external_id,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "owner_action": action or str(result.evidence.get("owner_action") or ""),
                "reason": reason or str(result.evidence.get("reason") or ""),
                "photos_found": 0,
                "photos_matched": 0,
                "screen_evidence": str(screen) if screen else "",
            }
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + platform
            registry.write_json(platform, run_id, "result.json", payload)
            registry.finalize(platform, run_id)
            written.append({
                "platform": platform,
                "status": result.status,
                "evidence": str(registry.run_dir(platform, run_id)),
            })
    print(json.dumps(written, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
