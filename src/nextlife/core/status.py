"""Status lifecycle and single state transition table for NextLife items."""

from enum import StrEnum

from nextlife.core.exceptions import NextLifeError


class ItemStatus(StrEnum):
    DRAFT = "DRAFT"
    NEW = "NEW"
    ESTIMATED = "ESTIMATED"
    PHOTOGRAPHED = "PHOTOGRAPHED"
    DESCRIBED = "DESCRIBED"
    IN_WARRANTY = "IN_WARRANTY"
    RESEARCHED = "RESEARCHED"
    MEDIA_READY = "MEDIA_READY"
    READY_FOR_SALE = "READY_FOR_SALE"
    PUBLISHED = "PUBLISHED"
    RESERVED = "RESERVED"
    SOLD = "SOLD"
    RETURNED = "RETURNED"
    ARCHIVED = "ARCHIVED"
    DISPOSED = "DISPOSED"
    DELETED = "DELETED"
    CANCELLED = "CANCELLED"
    # Legacy statuses for migration / backwards compatibility
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PARTIALLY_PUBLISHED = "PARTIALLY_PUBLISHED"
    BLOCKED = "BLOCKED"


class ListingStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    UPDATING = "UPDATING"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNPUBLISH_FAILED = "UNPUBLISH_FAILED"
    ARCHIVED = "ARCHIVED"


class InvalidStatusTransitionError(NextLifeError):
    """Raised when an item transition from current to target status is illegal."""


# The single canonical transition table for items.
#
# ТЗ «Склад» вместо «Ленты» §1.2:
# - Переход в SOLD разрешён из PUBLISHED, PARTIALLY_PUBLISHED, RESERVED,
#   APPROVED, DRAFT, AWAITING_APPROVAL (продажа может пройти вне системы).
# - Из любого статуса разрешён переход в DELETED.
# - DELETED освобождает код так же, как ARCHIVED (через code_release_days).
TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.NEW: frozenset(
        {
            ItemStatus.ESTIMATED,
            ItemStatus.PHOTOGRAPHED,
            ItemStatus.DESCRIBED,
            ItemStatus.READY_FOR_SALE,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.ESTIMATED: frozenset(
        {
            ItemStatus.PHOTOGRAPHED,
            ItemStatus.DESCRIBED,
            ItemStatus.READY_FOR_SALE,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.PHOTOGRAPHED: frozenset(
        {
            ItemStatus.DESCRIBED,
            ItemStatus.READY_FOR_SALE,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.DESCRIBED: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.IN_WARRANTY: frozenset(
        {
            ItemStatus.RETURNED,
            ItemStatus.ARCHIVED,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.DRAFT: frozenset(
        {
            ItemStatus.RESEARCHED,
            ItemStatus.MEDIA_READY,
            ItemStatus.READY_FOR_SALE,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.RESEARCHED: frozenset(
        {
            ItemStatus.MEDIA_READY,
            ItemStatus.READY_FOR_SALE,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.MEDIA_READY: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.AWAITING_APPROVAL,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.READY_FOR_SALE: frozenset(
        {
            ItemStatus.PUBLISHED,
            ItemStatus.RESERVED,
            ItemStatus.SOLD,
            ItemStatus.MEDIA_READY,
            ItemStatus.DRAFT,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.PUBLISHED: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.RESERVED,
            ItemStatus.SOLD,
            ItemStatus.ARCHIVED,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.RESERVED: frozenset(
        {
            ItemStatus.PUBLISHED,
            ItemStatus.READY_FOR_SALE,
            ItemStatus.SOLD,
            ItemStatus.RETURNED,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.SOLD: frozenset(
        {
            ItemStatus.RETURNED,
            ItemStatus.ARCHIVED,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
        }
    ),
    ItemStatus.RETURNED: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.PUBLISHED,
            ItemStatus.ARCHIVED,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.ARCHIVED: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
        }
    ),
    ItemStatus.DISPOSED: frozenset(
        {
            ItemStatus.DELETED,
        }
    ),
    ItemStatus.DELETED: frozenset(),  # Terminal
    ItemStatus.CANCELLED: frozenset({ItemStatus.DELETED, ItemStatus.DISPOSED}),
    # Backwards-compatibility transitions for legacy statuses:
    ItemStatus.AWAITING_APPROVAL: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.APPROVED,
            ItemStatus.MEDIA_READY,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.APPROVED: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.PUBLISHING,
            ItemStatus.AWAITING_APPROVAL,
            ItemStatus.SOLD,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.PUBLISHING: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.PUBLISHED,
            ItemStatus.PARTIALLY_PUBLISHED,
            ItemStatus.BLOCKED,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.PARTIALLY_PUBLISHED: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.PUBLISHING,
            ItemStatus.PUBLISHED,
            ItemStatus.RESERVED,
            ItemStatus.SOLD,
            ItemStatus.BLOCKED,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.BLOCKED: frozenset(
        {
            ItemStatus.READY_FOR_SALE,
            ItemStatus.PUBLISHING,
            ItemStatus.DISPOSED,
            ItemStatus.DELETED,
            ItemStatus.CANCELLED,
        }
    ),
}


def can_transition(current_status: str | ItemStatus, target_status: str | ItemStatus) -> bool:
    curr = ItemStatus(current_status)
    target = ItemStatus(target_status)
    return target in TRANSITIONS.get(curr, frozenset())


def validate_transition(current_status: str | ItemStatus, target_status: str | ItemStatus) -> None:
    curr = ItemStatus(current_status)
    target = ItemStatus(target_status)
    if not can_transition(curr, target):
        allowed = [s.value for s in TRANSITIONS.get(curr, frozenset())]
        raise InvalidStatusTransitionError(
            f"Cannot transition item from '{curr.value}' to '{target.value}'. Allowed transitions: {allowed}"
        )


# Русские названия статусов для экранов (TZ-NextLife-UI-N2 §4 V4, P-E15).
# Сырые токены — внутренняя механика (Э-6): пользователь видит только
# осмысленные названия. Неизвестный токен выводится как есть (не выдумываем).
STATUS_LABELS_RU: dict[str, str] = {
    "NEW": "Новый",
    "ESTIMATED": "Оценён",
    "PHOTOGRAPHED": "С фото",
    "DESCRIBED": "С описанием",
    "IN_WARRANTY": "На гарантии",
    "DRAFT": "Черновик",
    "RESEARCHED": "Разведка",
    "MEDIA_READY": "Медиа готово",
    "AWAITING_APPROVAL": "Ожидает утверждения",
    "APPROVED": "Утверждён",
    "PUBLISHING": "Публикуется",
    "PUBLISHED": "Опубликован",
    "PARTIALLY_PUBLISHED": "Частично опубликован",
    "BLOCKED": "Блокирован",
    "RESERVED": "Забронирован",
    "SOLD": "Продан",
    "RETURNED": "Возвращён",
    "ARCHIVED": "В архиве",
    "CANCELLED": "Отменён",
    "DELETED": "Удалён",
    "ACTIVE": "Активен",  # legacy-статус предмета (вне переходов)
}

DEAL_STATUS_LABELS_RU: dict[str, str] = {
    "ACTIVE": "Активная",
    "PAID": "Оплачена",
    "DISPATCHED": "Отправлена",
    "COMPLETED": "Завершена",
    "DISPUTED": "Спор",
    "RETURNED": "Возврат",
    "CANCELLED": "Отменена",
}


def status_label_ru(status: str | None) -> str:
    """Русское название статуса предмета/лота; неизвестный — как есть."""
    if not status:
        return ""
    return STATUS_LABELS_RU.get(status, status)


def deal_status_label_ru(status: str | None) -> str:
    """Русское название статуса сделки; неизвестный — как есть."""
    if not status:
        return ""
    return DEAL_STATUS_LABELS_RU.get(status, status)
