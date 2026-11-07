"""Operational publish / withdraw / status (TZ Connectors 5.4).

``publish(lot_data, dry_run)`` on each connector stays the dry-run
contract used by the wizard. Live work goes through this module so a
connector cannot report a listing that the independent verifier has
not accepted. Status values are the closed set from section 2.1.
"""

from __future__ import annotations

import socket
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.connectors.base import PublishResult
from nextlife.core.config import get_data_dir, load_config
from nextlife.core.status import ListingStatus
from nextlife.services.category_gate import category_rejection
from nextlife.storage.models import Item, Listing

_ANDROID = {
    "bazos_cz",
    "bazos_sk",
    "sbazar_cz",
    "vinted",
    "kleinanzeigen_de",
    "willhaben_at",
    "olx_pl",
}
_SEARCH_ONLY = {"kleinanzeigen_de", "willhaben_at", "olx_pl"}
_DEAD = {ListingStatus.ARCHIVED.value, ListingStatus.FAILED.value, "DELETED"}


def storage_state_path(platform: str) -> Path:
    return get_data_dir() / "secrets" / "storage" / f"{platform}.json"


def broker_reachable(host: str = "127.0.0.1", port: int = 8090, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _result(
    status: str,
    *,
    external_id: str = "",
    public_url: str = "",
    evidence_dir: str = "",
    **evidence: object,
) -> PublishResult:
    return PublishResult(
        external_id=external_id,
        external_url=public_url,
        evidence=dict(evidence),
        status=status,
        public_url=public_url,
        evidence_dir=evidence_dir,
    )


def publish(session: Session, lot_id: str, platform: str) -> PublishResult:
    """Publish ``lot_id`` on ``platform`` or return an honest non-success status."""
    item = session.get(Item, lot_id)
    if item is None:
        return _result("FAILED", reason=f"лот {lot_id} не найден")
    config = load_config()
    mp = config.marketplaces.get(platform)
    if mp is None:
        return _result("FAILED", reason=f"площадка {platform} не настроена")

    reason = category_rejection(mp, item.category_key)
    if reason:
        return _result("NOT_ALLOWED", reason=reason)

    existing = session.scalar(
        select(Listing)
        .where(
            Listing.item_code == lot_id,
            Listing.marketplace_key == platform,
            Listing.external_url.is_not(None),
            Listing.status.notin_(_DEAD),
        )
        .order_by(Listing.created_at.desc())
    )
    if existing is not None and (existing.external_url or "").strip():
        return _result(
            "SUBMITTED_UNVERIFIED",
            external_id=existing.external_id or "",
            public_url=existing.external_url or "",
            reason="живое объявление этого лота уже записано, новое не создаётся",
            listing_id=existing.id,
        )

    if platform == "facebook_marketplace":
        return _result(
            "BLOCKED",
            reason="автоматическая публикация Facebook запрещена; нужен пакет и ручное подтверждение",
            owner_action="Открыть карточку лота, скачать пакет и опубликовать вручную",
        )

    if platform in _SEARCH_ONLY:
        return _result(
            "BLOCKED",
            reason="публикация не выполнялась: нет сессии и нет свежего доказательства барьера формы",
            owner_action="Не покупать номер. Подтвердить, что площадка остаётся только для поиска",
        )

    if platform == "bazos_sk":
        return _result(
            "BLOCKED",
            reason=(
                "в карточке лота нет отдельной цены EUR; курс пересчитывать нельзя; "
                "приём чешского PSČ формой Bazoš.sk этим запуском не подтверждён"
            ),
            owner_action="Записать цену 1050 EUR в карточку лота на /ui/stock/130",
        )

    if platform == "aukro_cz":
        from sqlalchemy import func

        from nextlife.storage.models.shipping import DeliveryOption

        shipping_count = session.scalar(
            select(func.count()).select_from(DeliveryOption).where(DeliveryOption.item_code == lot_id)
        )
        if not shipping_count:
            return _result(
                "BLOCKED",
                reason="в карточке лота нет способов доставки, форма Aukro не отправлялась",
                owner_action="Указать способы доставки на /ui/stock/130",
            )

    if platform in _SEARCH_ONLY:
        return _result(
            "BLOCKED",
            reason="публикация не отправлялась: барьер формы снимается отдельно, подставные данные запрещены",
            owner_action="Ничего не покупать. Площадка остаётся для поиска, пока нет доказательства правила",
        )

    if platform in _ANDROID or getattr(mp, "access_mode", "") == "ANDROID":
        if not broker_reachable():
            return _result(
                "BLOCKED",
                reason="PhoneBroker не отвечает на 127.0.0.1:8090",
                owner_action="В каталоге PhoneBroker выполнить sudo make install и вернуть аренду телефона",
            )
        return _result(
            "BLOCKED",
            reason="телефон отвечает, форма объявления в этом запуске не отправлялась",
            owner_action=f"Проверить вход в приложение {platform}. Страница /ui/connect",
        )

    if not storage_state_path(platform).is_file():
        return _result(
            "BLOCKED",
            reason="нет сохранённой веб-сессии площадки",
            owner_action=f"Войти в аккаунт на странице /ui/connect?platform={platform}",
        )

    return _result(
        "BLOCKED",
        reason="сессия есть, отправка формы в этом запуске не выполнялась",
        owner_action=f"Повторить публикацию {platform} после проверки формы",
    )


def withdraw(session: Session, listing_id: str) -> PublishResult:
    listing = session.get(Listing, listing_id)
    if listing is None:
        return _result("FAILED", reason=f"объявление {listing_id} не найдено")
    if not (listing.external_url or "").strip():
        listing.status = ListingStatus.FAILED.value
        listing.last_error_text = "снятие не требовалось: публичного URL нет"
        return _result(
            "FAILED",
            external_id=listing.external_id or "",
            reason="публичного URL нет, снимать на площадке нечего",
            listing_id=listing.id,
        )
    return _result(
        "BLOCKED",
        external_id=listing.external_id or "",
        public_url=listing.external_url or "",
        reason="снятие на площадке не выполнялось: нет сессии",
        owner_action=f"Войти на /ui/connect?platform={listing.marketplace_key} и снять объявление",
        listing_id=listing.id,
    )


def status(session: Session, listing_id: str) -> PublishResult:
    listing = session.get(Listing, listing_id)
    if listing is None:
        return _result("FAILED", reason=f"объявление {listing_id} не найдено")
    evidence = listing.publish_evidence or {}
    recorded = str(evidence.get("live_status") or "")
    if recorded in {
        "LIVE_VERIFIED",
        "PENDING_OWNER_CHECK",
        "ACCEPTED_BY_OWNER",
        "PENDING_MODERATION",
        "FAILED",
        "BLOCKED",
        "NOT_ALLOWED",
        "SUBMITTED_UNVERIFIED",
    }:
        return _result(
            recorded,
            external_id=listing.external_id or "",
            public_url=listing.external_url or "",
            evidence_dir=str(evidence.get("evidence_dir") or ""),
            listing_id=listing.id,
        )
    if (listing.external_url or "").strip():
        return _result(
            "SUBMITTED_UNVERIFIED",
            external_id=listing.external_id or "",
            public_url=listing.external_url or "",
            listing_id=listing.id,
        )
    return _result("NOT_STARTED", listing_id=listing.id)
