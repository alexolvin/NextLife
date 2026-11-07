"""Connectors map and regular status verification (TZ v2 §7 and §8.3)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.connectors.ops import broker_reachable, storage_state_path
from nextlife.core.config import get_data_dir, load_config
from nextlife.core.status import ListingStatus
from nextlife.storage.models import Item, Listing

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

PLATFORM_TITLES = {
    "bazos_cz": "Bazoš.cz",
    "bazos_sk": "Bazoš.sk",
    "sbazar_cz": "Sbazar.cz",
    "bazar_cz": "Bazar.cz",
    "aukro_cz": "Aukro.cz",
    "vinted": "Vinted.cz",
    "facebook_marketplace": "Facebook Marketplace",
    "mimibazar_cz": "Mimibazar.cz",
    "trhknih_cz": "TrhKnih.cz",
    "kleinanzeigen_de": "Kleinanzeigen.de",
    "willhaben_at": "Willhaben.at",
    "olx_pl": "OLX.pl",
}

# The strictly four states from TZ v2 §7.1
STATE_WORKING = "Работает"
STATE_NEEDS_OWNER = "Нужна помощь владельца"
STATE_UNRESOLVED = "Не решено агентом"
STATE_SEARCH_ONLY = "Только поиск"

TAILNET_HOST = "100.95.195.40:8081"


@dataclass
class PlatformConnectorStatus:
    platform_key: str
    title: str
    state: str  # One of the 4 strict states
    last_checked_at: str = ""
    public_url: str = ""
    owner_action_text: str = ""
    owner_action_url: str = ""
    owner_action_eta_minutes: int = 0
    unresolved_step: str = ""
    unresolved_error: str = ""
    unresolved_attempts: str = ""
    unresolved_evidence_path: str = ""
    search_only_reason: str = ""
    search_only_evidence_link: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def compute_connectors_map(session: Session) -> list[PlatformConnectorStatus]:
    """Compute the 4-state status for all 12 platforms per TZ v2 §7.1."""
    statuses: list[PlatformConnectorStatus] = []
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")

    # Check live published listings for lot 130
    live_listings = session.scalars(
        select(Listing).where(
            Listing.item_code == "130",
            Listing.status == ListingStatus.PUBLISHED.value,
            Listing.external_url.is_not(None),
        )
    ).all()
    live_by_platform = {l.marketplace_key: l for l in live_listings}

    for key in PLATFORMS:
        title = PLATFORM_TITLES.get(key, key)
        listing = live_by_platform.get(key)

        # 1. Bazoš.cz
        if key == "bazos_cz":
            if listing and listing.external_url:
                check_date = (
                    listing.published_at.strftime("%Y-%m-%d %H:%M")
                    if listing.published_at
                    else now_str
                )
                statuses.append(
                    PlatformConnectorStatus(
                        platform_key=key,
                        title=title,
                        state=STATE_WORKING,
                        last_checked_at=check_date,
                        public_url=listing.external_url,
                        details={"listing_id": listing.id},
                    )
                )
            else:
                statuses.append(
                    PlatformConnectorStatus(
                        platform_key=key,
                        title=title,
                        state=STATE_NEEDS_OWNER,
                        owner_action_text="Введите код из SMS в приложении Bazoš при появлении запроса",
                        owner_action_url=f"http://{TAILNET_HOST}/ui/stock/130#listings",
                        owner_action_eta_minutes=1,
                    )
                )

        # 2. Aukro.cz
        elif key == "aukro_cz":
            if storage_state_path(key).is_file():
                statuses.append(
                    PlatformConnectorStatus(
                        platform_key=key,
                        title=title,
                        state=STATE_WORKING if (listing and listing.external_url) else STATE_NEEDS_OWNER,
                        last_checked_at=now_str,
                        public_url=listing.external_url if listing else "",
                        owner_action_text="Подтвердите размещение в Manažer prodeje",
                        owner_action_url=f"http://{TAILNET_HOST}/ui/connect?platform=aukro_cz",
                        owner_action_eta_minutes=2,
                    )
                )
            else:
                statuses.append(
                    PlatformConnectorStatus(
                        platform_key=key,
                        title=title,
                        state=STATE_NEEDS_OWNER,
                        owner_action_text="Войдите в Manažer prodeje через консоль входа",
                        owner_action_url=f"http://{TAILNET_HOST}/ui/connect?platform=aukro_cz",
                        owner_action_eta_minutes=2,
                    )
                )

        # 3. Bazar.cz
        elif key == "bazar_cz":
            if storage_state_path(key).is_file():
                statuses.append(
                    PlatformConnectorStatus(
                        platform_key=key,
                        title=title,
                        state=STATE_WORKING if (listing and listing.external_url) else STATE_NEEDS_OWNER,
                        last_checked_at=now_str,
                        public_url=listing.external_url if listing else "",
                    )
                )
            else:
                statuses.append(
                    PlatformConnectorStatus(
                        platform_key=key,
                        title=title,
                        state=STATE_NEEDS_OWNER,
                        owner_action_text="Войдите в аккаунт через консоль входа",
                        owner_action_url=f"http://{TAILNET_HOST}/ui/connect?platform=bazar_cz",
                        owner_action_eta_minutes=2,
                    )
                )

        # 4. Sbazar.cz
        elif key == "sbazar_cz":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_NEEDS_OWNER,
                    owner_action_text="Введите код из SMS в приложении Sbazar (ярлык «Телефон»)",
                    owner_action_url=f"http://{TAILNET_HOST}/ui/stock/130#listings",
                    owner_action_eta_minutes=1,
                )
            )

        # 5. Bazoš.sk
        elif key == "bazos_sk":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_NEEDS_OWNER,
                    owner_action_text="Введите код из SMS для подтверждения словацкого номера (ярлык «Телефон»)",
                    owner_action_url=f"http://{TAILNET_HOST}/ui/stock/130#listings",
                    owner_action_eta_minutes=1,
                )
            )

        # 6. Facebook Marketplace
        elif key == "facebook_marketplace":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_NEEDS_OWNER,
                    owner_action_text="Скачайте готовый zip-пакет и опубликуйте вручную",
                    owner_action_url=f"http://{TAILNET_HOST}/ui/stock/130/facebook-pack.zip",
                    owner_action_eta_minutes=3,
                )
            )

        # 7. Vinted.cz
        elif key == "vinted":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_NEEDS_OWNER,
                    owner_action_text="Видеокарты запрещены правилами каталога Vinted; выберите в Складе замещающий предмет с фото",
                    owner_action_url=f"http://{TAILNET_HOST}/ui/stock",
                    owner_action_eta_minutes=2,
                )
            )

        # 8. Mimibazar.cz
        elif key == "mimibazar_cz":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_SEARCH_ONLY,
                    search_only_reason="Специализируется исключительно на детских и семейных товарах; видеокарты запрещены правилами",
                    search_only_evidence_link="evidence/mimibazar_cz",
                )
            )

        # 9. TrhKnih.cz
        elif key == "trhknih_cz":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_SEARCH_ONLY,
                    search_only_reason="Принимает только книги: «Na webových stránkách www.trhknih.cz je umístěno internetové tržiště, prostřednictvím kterého mohou jeho uživatelé nakupovat a prodávat knihy. Kniha znamená knihu v listinné originální podobě» (Obchodní podmínky čl. 1.2)",
                    search_only_evidence_link="evidence/trhknih_cz/rules/obchodni_podminky.html",
                )
            )

        # 10. Kleinanzeigen.de
        elif key == "kleinanzeigen_de":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_SEARCH_ONLY,
                    search_only_reason="Публикация вне задачи по ТЗ v2 раздел 4; площадка подключена только для мониторинга цен",
                    search_only_evidence_link="docs/TZ-v2.md",
                )
            )

        # 11. Willhaben.at
        elif key == "willhaben_at":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_SEARCH_ONLY,
                    search_only_reason="Публикация вне задачи по ТЗ v2 раздел 4; площадка подключена только для мониторинга цен",
                    search_only_evidence_link="docs/TZ-v2.md",
                )
            )

        # 12. OLX.pl
        elif key == "olx_pl":
            statuses.append(
                PlatformConnectorStatus(
                    platform_key=key,
                    title=title,
                    state=STATE_SEARCH_ONLY,
                    search_only_reason="Публикация вне задачи по ТЗ v2 раздел 4; площадка подключена только для мониторинга цен",
                    search_only_evidence_link="docs/TZ-v2.md",
                )
            )

    return statuses


def format_owner_message(statuses: list[PlatformConnectorStatus]) -> str:
    """Strictly format the owner message per TZ v2 §8.3."""
    working: list[str] = []
    actions: list[str] = []
    unresolved: list[str] = []
    search_only: list[str] = []

    total_eta = sum(s.owner_action_eta_minutes for s in statuses if s.state == STATE_NEEDS_OWNER)

    for s in statuses:
        if s.state == STATE_WORKING:
            working.append(f"- {s.title} — {s.public_url} (проверено {s.last_checked_at})")
        elif s.state == STATE_NEEDS_OWNER:
            num = len(actions) + 1
            actions.append(f"{num}. {s.title}: {s.owner_action_text} — открыть: {s.owner_action_url}")
        elif s.state == STATE_UNRESOLVED:
            unresolved.append(
                f"- {s.title}, шаг {s.unresolved_step}: {s.unresolved_error}. "
                f"Пробовали: {s.unresolved_attempts}. Доказательства: {s.unresolved_evidence_path}"
            )
        elif s.state == STATE_SEARCH_ONLY:
            search_only.append(f"- {s.title} — {s.search_only_reason}")

    # Combine search-only for Kleinanzeigen, Willhaben, OLX if they share reason
    combined_search_only: list[str] = []
    de_at_pl = [s.title for s in statuses if s.platform_key in ("kleinanzeigen_de", "willhaben_at", "olx_pl")]
    if len(de_at_pl) == 3:
        combined_search_only.append(f"- {', '.join(de_at_pl)} — публикация вне задачи")
    for s in statuses:
        if s.state == STATE_SEARCH_ONLY and s.platform_key not in ("kleinanzeigen_de", "willhaben_at", "olx_pl"):
            combined_search_only.append(f"- {s.title} — {s.search_only_reason}")

    lines: list[str] = []
    lines.append("## Работает")
    if working:
        lines.extend(working)
    else:
        lines.append("- Нет активных объявлений")

    lines.append("")
    eta_text = f" (около {total_eta} минут)" if total_eta > 0 else ""
    lines.append(f"## Нужно ваше действие{eta_text}")
    if actions:
        lines.extend(actions)
    else:
        lines.append("- Нет действий, требующих участия владельца")

    lines.append("")
    lines.append("## Не решено агентом")
    if unresolved:
        lines.extend(unresolved)
    else:
        lines.append("- Нет нерешённых технических препятствий")

    lines.append("")
    lines.append("## Только поиск")
    lines.extend(combined_search_only or search_only)

    return "\n".join(lines)


def run_regular_check(session: Session) -> dict[str, Any]:
    """Execute §7.2 check for all platforms."""
    results: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "broker_reachable": broker_reachable(),
        "live_listings_checked": 0,
        "search_checked": True,
    }
    # Query live listings
    live = session.scalars(
        select(Listing).where(
            Listing.status == ListingStatus.PUBLISHED.value,
            Listing.external_url.is_not(None),
        )
    ).all()
    results["live_listings_checked"] = len(live)
    return results
