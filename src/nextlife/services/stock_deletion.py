"""Stock Item Deletion service with Closed-Loop Marketplace Verification.

TZ «Склад» §1.3, §3.4 and Project Ideology (Closed-Loop Marketplace Verification):
- Deletes all subordinate objects: listings, searches (research runs), photos.
- Active marketplace listings MUST be confirmed removed from the external platform
  before the item can be deleted.
- If any marketplace unpublish fails or ad remains live, deletion is ABORTED,
  and the user receives a detailed explanation with links for manual removal.
- Module length strictly <= 400 lines (Rule K-1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.status import ItemStatus, validate_transition
from nextlife.media.ops import delete_photo_and_derived
from nextlife.obs.audit import record_audit_event
from nextlife.services.listings_reconcile import check_url_is_live
from nextlife.services.lot_delete import LotDeletionBlockedError, can_delete_lot
from nextlife.storage.models import Deal, Item, ItemPhoto, Listing, Lot, ResearchResult, ResearchRun


class StockDeletionUnpublishError(Exception):
    """Raised when marketplace unpublish fails during item deletion.

    Prevents phantom sales by refusing warehouse item deletion until listings are removed.
    """

    def __init__(self, message: str, failed_listings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.failed_listings = failed_listings or []


def unpublish_active_listings_closed_loop(
    session: Session,
    item_code: str,
    *,
    actor: str = "OWNER",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Synchronously unpublish all live listings with Closed-Loop verification.

    Returns (successful_results, failed_results).
    """
    from nextlife.connectors.registry import get_connector

    cfg = load_config()
    active_statuses = ["PUBLISHED", "PARTIALLY_PUBLISHED", "UPDATING", "UNPUBLISH_FAILED", "ACTIVE"]
    listings = session.scalars(
        select(Listing).where(
            Listing.item_code == item_code,
            Listing.status.in_(active_statuses),
        )
    ).all()

    now_utc = datetime.now(UTC)
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for listing in listings:
        mp_key = listing.marketplace_key
        external_url = listing.external_url or ""
        unpub_err: str | None = None

        try:
            # 1. Platform-specific unpublishing
            if mp_key in ("bazos_cz", "bazos_sk"):
                from nextlife.services.bazos_android_publish import bazos_android_delete_listing

                res = bazos_android_delete_listing(
                    title=listing.title or "",
                    external_id=listing.external_id,
                    platform=mp_key,
                    dry_run=False,
                )
                if not res.get("ok"):
                    unpub_err = "; ".join(res.get("errors") or ["Удаление не подтверждено приложением Bazoš"])
            else:
                conn = get_connector(mp_key, cfg)
                if hasattr(conn, "unpublish"):
                    ok = conn.unpublish(listing.external_id or "")
                    if ok is False:
                        unpub_err = f"Коннектор площадки {mp_key} сообщил об ошибке снятия."

            # 2. Closed-Loop verification: check if URL is still live
            if not unpub_err and external_url and not external_url.rstrip("/").endswith((".cz", ".sk", ".de", ".at", ".com", ".pl")):
                if check_url_is_live(external_url):
                    unpub_err = "Проверка показала: объявление всё ещё активно и доступно на площадке!"

        except Exception as exc:
            unpub_err = str(exc)

        if unpub_err:
            listing.status = "UNPUBLISH_FAILED"
            listing.last_error_text = f"Ошибка снятия с площадки: {unpub_err}"
            listing.last_error_hint = "Объявление продолжает экспонироваться! Снимите его вручную по ссылке."
            record_audit_event(
                session=session,
                actor=actor,
                action="UNPUBLISH_FAILED",
                entity="listing",
                entity_id=listing.id,
                data={"marketplace": mp_key, "error": unpub_err, "external_url": external_url},
            )
            failures.append({
                "listing_id": listing.id,
                "marketplace_key": mp_key,
                "external_url": external_url,
                "error": unpub_err,
            })
        else:
            listing.status = "ARCHIVED"
            listing.archived_at = now_utc
            listing.last_error_text = None
            listing.last_error_hint = None
            record_audit_event(
                session=session,
                actor=actor,
                action="UNPUBLISH_CONFIRMED",
                entity="listing",
                entity_id=listing.id,
                data={"marketplace": mp_key, "external_url": external_url},
            )
            successes.append({
                "listing_id": listing.id,
                "marketplace_key": mp_key,
                "external_url": external_url,
            })

    session.flush()
    return successes, failures


def delete_stock_item_closed_loop(
    session: Session,
    code: str,
    confirm_listings: bool,
    confirm_deals: bool = False,
    actor: str = "OWNER",
) -> dict[str, Any]:
    """Удаление предмета Склада с принципом Closed-Loop Marketplace Verification.

    Порядок:
    1. Проверка активных сделок (блокировка при незавершённых сделках).
    2. Проверка подтверждения снятия объявлений.
    3. Фактическое снятие активных объявлений с верификацией площадки.
       При ЛЮБОМ сбое снятия -> ОТКАЗ В УДАЛЕНИИ (предмет и фото остаются нетронутыми).
    4. Удаление всех подчинённых объектов (неактивные объявления, поиски research_run, фото).
    5. Soft-delete предмета и лота (DELETED).
    """
    from nextlife.services.stock import (
        StockDeletionNeedsConfirmationError,
        StockItemNotFoundError,
        _active_listings,
        _get_item,
    )

    item = _get_item(session, code)
    lot = session.scalar(select(Lot).where(Lot.code == code))

    # 1. Проверка активных сделок
    can_delete, reason = can_delete_lot(session, code)
    if not can_delete:
        if confirm_deals:
            item_id = lot.item_id if lot else code
            listings = session.scalars(select(Listing).where(Listing.item_code == item_id)).all()
            listing_ids = [lst.id for lst in listings]
            conditions = []
            if listing_ids:
                conditions.append(Deal.listing_id.in_(listing_ids))
            if lot:
                conditions.append(Deal.lot_id == lot.id)
            if conditions:
                active_deals = session.scalars(
                    select(Deal).where(
                        or_(*conditions),
                        Deal.status.in_(["ACTIVE", "OPEN", "IN_PROGRESS", "PAID", "SHIPPED"]),
                        Deal.closed_at.is_(None),
                    )
                ).all()
                now_utc = datetime.now(UTC)
                for d in active_deals:
                    d.status = "CANCELLED"
                    d.closed_at = now_utc
            session.flush()
        else:
            raise LotDeletionBlockedError(reason or "Активная сделка блокирует удаление.")

    # 2. Проверка подтверждения наличия активных объявлений
    active_listings = _active_listings(session, code)
    if active_listings and not confirm_listings:
        raise StockDeletionNeedsConfirmationError(
            f"Нужно подтверждение снятия {len(active_listings)} объявлений."
        )

    # 3. Фактическое снятие с площадок с Closed-Loop верификацией
    successes, failures = unpublish_active_listings_closed_loop(session, code, actor=actor)
    if failures:
        # ОТКАЗ В УДАЛЕНИИ: во избежание продажи отсутствующего товара
        reasons = []
        for f in failures:
            mp = f.get("marketplace_key")
            err = f.get("error") or "Сбой связи с площадкой"
            url = f.get("external_url") or ""
            link_hint = f" (ссылка: {url})" if url else ""
            reasons.append(f"• {mp}: {err}{link_hint}")
        details = "\n".join(reasons)

        session.commit()  # commit the UNPUBLISH_FAILED listing status and audit events
        raise StockDeletionUnpublishError(
            f"Отказ в удалении предмета #{code}: не удалось подтвердить снятие объявлений с площадок:\n{details}\n\n"
            f"Во избежание продажи отсутствующего товара удаление предмета и фото отменено.\n"
            f"Рекомендации по устранению:\n"
            f"1. Снимите объявление(я) вручную по ссылкам выше.\n"
            f"2. Проверьте подключение PhoneBroker / авторизацию на площадке и повторите попытку.",
            failed_listings=failures,
        )

    # 4. Удаление/архивация всех остальных объявлений предмета (черновики, неразмещённые)
    session.execute(
        delete(Listing).where(
            Listing.item_code == code,
            Listing.status.in_(["DRAFT", "APPROVED", "FAILED", "QUEUED"]),
        )
    )

    # 5. Удаление подчинённых поисков (ResearchRun и ResearchResult)
    run_ids = session.scalars(select(ResearchRun.id).where(ResearchRun.item_code == code)).all()
    if run_ids:
        session.execute(delete(ResearchResult).where(ResearchResult.run_id.in_(run_ids)))
        session.execute(delete(ResearchRun).where(ResearchRun.id.in_(run_ids)))

    # 6. Soft delete: lot DELETED, item DELETED
    if lot is not None:
        lot.status = "DELETED"
    validate_transition(ItemStatus(item.status), ItemStatus.DELETED)
    item.status = ItemStatus.DELETED.value

    # 7. Удаление фото из БД и сбор путей файлов
    photos = session.scalars(select(ItemPhoto).where(ItemPhoto.item_code == code)).all()
    photo_ids = {p.id for p in photos}
    roots = [p.id for p in photos if p.derived_from_id is None or p.derived_from_id not in photo_ids]
    files: list[str] = []
    for pid in roots:
        files.extend(delete_photo_and_derived(session, code, pid))

    record_audit_event(
        session=session,
        actor=actor,
        action="STOCK_ITEM_DELETED",
        entity="item",
        entity_id=code,
        data={
            "title": item.title,
            "unpublished_count": len(successes),
            "photos_deleted": len(files),
            "searches_cleaned": len(run_ids),
        },
    )
    session.commit()

    # 8. Физическое удаление файлов фотографий на диске — строго после успешного коммита
    removed: list[str] = []
    for path in files:
        try:
            Path(path).unlink(missing_ok=True)
            removed.append(path)
        except OSError:
            pass

    return {
        "code": code,
        "status": "DELETED",
        "unpublished": successes,
        "photos_deleted": len(removed),
        "searches_cleaned": len(run_ids),
    }
