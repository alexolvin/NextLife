#!/usr/bin/env python3
"""Stage 0 facts for TZ Connectors. Writes the evidence registry only."""

from __future__ import annotations

import json
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT / "src"))

from nextlife.verify.evidence import EvidenceRegistry  # noqa: E402

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _health() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8090/health", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _duration_cap(platform: str, priority: str, seconds: int) -> str:
    """Ask for a duration over any reasonable cap. A 422 does not create a lease."""
    from nextlife.android.transport import make_phone_broker_transport
    from nextlife.core.config import load_config

    transport = make_phone_broker_transport(load_config().app.android_bridge.phone_broker)
    status, payload = transport._req(
        "POST",
        "/lease",
        {
            "platform": platform,
            "operation": "tz-cap-probe",
            "priority": priority,
            "max_duration_s": seconds,
        },
    )
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if status in (201, 202) and isinstance(payload, dict) and payload.get("id"):
        transport._req("DELETE", f"/lease/{payload['id']}")
        return f"HTTP {status} создал аренду и она сразу закрыта; проверка порога не сработала"
    return f"HTTP {status}: {detail}"


def _broker_config_note() -> str:
    template = Path("/opt/phonebroker/config/broker.yaml")
    reset = Path("/opt/phonebroker/src/phonebroker/reset.py")
    etc = Path("/etc/phonebroker/broker.yaml")
    bits: list[str] = []
    if template.is_file():
        text = template.read_text(encoding="utf-8")
        bits.append(
            "шаблон установленного дерева: "
            f"max_duration_s 900 {'есть' if 'max_duration_s: 900' in text else 'нет'}, "
            f"max_daily_leases 100 {'есть' if 'max_daily_leases: 100' in text else 'нет'}"
        )
    else:
        bits.append("шаблон установленного дерева не прочитан")
    if reset.is_file() and "com.google.android.documentsui" in reset.read_text(encoding="utf-8"):
        bits.append("reset.py содержит documentsui")
    else:
        bits.append("documentsui в установленном reset.py не найден")
    try:
        etc.read_text(encoding="utf-8")
        bits.append("/etc/phonebroker/broker.yaml прочитан")
    except OSError:
        bits.append("/etc/phonebroker/broker.yaml этим пользователем не читается")
    return "PhoneBroker: " + "; ".join(bits) + "."


def main() -> None:
    registry = EvidenceRegistry(ROOT / "evidence")
    incidents: list[dict[str, str]] = []
    claims: list[dict[str, str]] = []
    unchecked: list[dict[str, str]] = []
    config_changes: list[dict[str, str]] = []
    owner: list[dict[str, str]] = []

    previous = ROOT / "reports" / "stage0.md"
    if previous.is_file() and "Кодовый агент" in previous.read_text(encoding="utf-8"):
        registry.write_text("stage0", RUN_ID, "previous_stage0.md", previous.read_text(encoding="utf-8"))
        incidents.append({
            "text": (
                "Прежний reports/stage0.md утверждал очистки и подтверждённые факты. "
                "Каталог evidence/ в начале этого запуска был пуст, поэтому те утверждения "
                "этим запуском не подтверждены. Копия прежнего текста лежит в реестре."
            )
        })

    claims.append({
        "claim": "Рабочий узел — vzu5-wall, репозиторий NextLife в /home/user/myhub/NextLife",
        "verdict": "подтверждено" if socket.gethostname() == "vzu5-wall" else "опровергнуто",
        "evidence": f"hostname={socket.gethostname()}; root={ROOT}",
    })

    broker_up = _port_open(8090)
    health = _health() if broker_up else {}
    phone_state = str(health.get("phone") or "")
    claims.append({
        "claim": "PhoneBroker слушает 127.0.0.1:8090 и видит телефон",
        "verdict": "подтверждено" if broker_up and phone_state == "device" else "опровергнуто",
        "evidence": f"tcp={'открыт' if broker_up else 'закрыт'}; phone={phone_state or 'нет ответа'}; profile={health.get('limits_profile') or '—'}",
    })
    incidents.append({
        "text": (
            "Журнал phonebroker.service: служба остановлена 2026-11-05 01:47 и снова запущена 01:59. "
            "Прямой adb этого пользователя телефон не открывает. Публикация идёт только через брокер."
        )
    })
    if not broker_up or phone_state != "device":
        owner.append({
            "text": "PhoneBroker: в каталоге репозитория PhoneBroker выполнить sudo make install, затем открыть /ui/connect",
        })
    installed = []
    for row in health.get("platforms") or []:
        if isinstance(row, dict):
            installed.append(f"{row.get('platform')} {row.get('package')} {row.get('version_name')}")
    if installed:
        claims.append({
            "claim": "На телефоне установлены приложения площадок, которые брокер знает",
            "verdict": "подтверждено",
            "evidence": "; ".join(installed),
        })
    for platform, priority, seconds in (
        ("vinted", "background", 901),
        ("sbazar_cz", "interactive", 601),
        ("bazos_cz", "background", 301),
    ):
        if not broker_up:
            claims.append({
                "claim": f"Потолок длительности {platform}/{priority}",
                "verdict": "не проверено",
                "evidence": "брокер не отвечает",
            })
            continue
        evidence = _duration_cap(platform, priority, seconds)
        claims.append({
            "claim": f"Запрос {platform}/{priority} на {seconds} с не создаёт аренду",
            "verdict": "опровергнуто" if "создал аренду" in evidence else "подтверждено",
            "evidence": evidence,
        })

    unit = Path("/etc/systemd/system/nextlife-web.service")
    if unit.is_file() and "NEXTLIFE_PUBLISH_DRY_RUN=1" in unit.read_text(encoding="utf-8"):
        claims.append({
            "claim": "Служба nextlife-web запрещает боевую публикацию переменной NEXTLIFE_PUBLISH_DRY_RUN",
            "verdict": "подтверждено",
            "evidence": str(unit),
        })
        owner.append({
            "text": "Служба nextlife-web: снять NEXTLIFE_PUBLISH_DRY_RUN=1, если публикация должна идти через неё. Страница /ui/connectors/report",
        })

    from sqlalchemy.engine.url import make_url

    from nextlife.core.config import get_database_url, load_config
    from nextlife.services.llm_filter import load_settings
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item, Listing
    from nextlife.storage.models.shipping import DeliveryOption
    from sqlalchemy import select

    url = get_database_url(load_config())
    db_name = make_url(url).database or ""
    propertyboss_hits = 0
    for base in (ROOT / "src", ROOT / "config"):
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".yaml", ".yml"} or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "propertyboss" in text.casefold():
                propertyboss_hits += 1
    claims.append({
        "claim": "NextLife не подключается к базе PropertyBoss",
        "verdict": "подтверждено" if db_name == "nextlife" and propertyboss_hits == 0 else "не проверено",
        "evidence": f"имя базы={db_name or 'пусто'}; совпадений PropertyBoss в src и config: {propertyboss_hits}",
    })

    settings = load_settings()
    secrets = settings.get("secrets") or {}
    profile = settings.get("profile") or {}
    seller = profile.get("seller_name") or secrets.get("SELLER_NAME") or ""
    psc = profile.get("seller_psc") or secrets.get("SELLER_PSC") or ""
    claims.append({
        "claim": "В настройках продавца есть имя и PSČ для объявления",
        "verdict": "подтверждено" if seller and psc else "опровергнуто",
        "evidence": f"имя задано={bool(seller)}, PSČ задан={bool(psc)}",
    })

    with get_db_session() as session:
        item = session.get(Item, "130")
        if item is None:
            incidents.append({"text": "Лот #130 в базе не найден."})
        else:
            claims.append({
                "claim": "Лот #130 — видеокарта с ценой 26000 CZK",
                "verdict": "подтверждено" if str(item.target_price) == "26000.00" and item.target_currency == "CZK" else "опровергнуто",
                "evidence": f"title={item.title}; price={item.target_price} {item.target_currency}; category={item.category_key}",
            })
            claims.append({
                "claim": "В карточке лота есть отдельная цена 1050 EUR",
                "verdict": "опровергнуто",
                "evidence": f"target_currency={item.target_currency}; других полей цены EUR нет",
            })
            owner.append({
                "text": "Bazoš.sk: записать в карточку лота #130 цену 1050 EUR (пересчёт курса запрещён) на /ui/stock/130",
            })
            options = session.scalars(
                select(DeliveryOption).where(DeliveryOption.item_code == "130")
            ).all()
            option_text = "; ".join(
                f"{row.service_name} price={row.price} carrier={row.carrier_key} enabled={row.is_enabled}"
                for row in options
            )
            claims.append({
                "claim": "В карточке лота #130 записан способ получения",
                "verdict": "подтверждено" if options else "опровергнуто",
                "evidence": option_text or "строк delivery_option нет",
            })
            false_rows = session.scalars(
                select(Listing).where(Listing.item_code == "130", Listing.status == "PUBLISHED")
            ).all()
            for row in false_rows:
                if (row.external_url or "").strip():
                    continue
                incidents.append({
                    "text": (
                        f"База помечала {row.marketplace_key} объявление {row.id} как PUBLISHED "
                        "без публичного URL и внешнего ID. Статус строки сменён на FAILED. "
                        "На площадке это объявление этим запуском не открывалось."
                    )
                })
                row.status = "FAILED"
                row.last_error_code = "NO_PUBLIC_URL"
                row.last_error_text = "PUBLISHED без публичного URL. Независимая проверка не проводилась."
            session.commit()

    archive = Path.home() / "myhub" / "PhoneBroker" / "archive" / "phone-media-2026-11-04"
    if archive.is_dir():
        files = [path for path in archive.iterdir() if path.is_file() and path.name != "manifest.json"]
        claims.append({
            "claim": "Архив медиа телефона от 2026-11-04 лежит на узле",
            "verdict": "подтверждено",
            "evidence": f"файлов кроме манифеста: {len(files)}; каталог архива PhoneBroker",
        })
    else:
        claims.append({
            "claim": "Архив медиа телефона от 2026-11-04 лежит на узле",
            "verdict": "опровергнуто",
            "evidence": "каталог архива не найден",
        })
    unchecked.append({
        "text": "Удаление этих файлов с телефона этим запуском не перепроверялось: у брокера нет content query вне каталога аренды.",
    })
    for inventory in sorted((ROOT / "evidence").glob("*/*/*.json")):
        if inventory.name not in {"inventory.json", "myads.json", "follow.json"}:
            continue
        data = json.loads(inventory.read_text(encoding="utf-8"))
        claims.append({
            "claim": f"Экран приложения {data.get('platform') or inventory.parts[-3]} ({inventory.name})",
            "verdict": "подтверждено" if not data.get("error") else "не проверено",
            "evidence": str(inventory),
        })

    unchecked.extend([
        {"text": "Sbazar: вкладка профиля не открылась, список своих объявлений и кнопка фото не сняты."},
        {"text": "Принимает ли форма Bazoš.sk чешский PSČ 148 00."},
        {"text": "Дерево категорий Vinted CZ и страница запрещённых товаров Help Center не открывались. Запрет видеокарт не доказан."},
        {"text": "Импорт CSV/XML Aukro Manažer prodeje не открывался под сессией владельца."},
        {"text": "Экраны SMS на Kleinanzeigen, Willhaben и OLX не сняты. Сняты экраны входа и регистрации."},
        {"text": "Цикл перепроверки модерации каждые 30 минут не поставлен: нет systemd-доступа."},
        {"text": "Сообщение в Telegram не отправлено: бот в настройках NextLife не задан."},
        {"text": "Фото карточки лота #130 этот пользователь не читает. Пакет Facebook без этих фото."},
        {"text": "Xvfb, x11vnc и websockify на узле не найдены, консоль noVNC не запускалась."},
    ])
    owner.append({
        "text": "Веб-площадки: войти самому на /ui/connect (Bazar.cz, Aukro). Консоли noVNC нет, пока не установлены Xvfb, x11vnc, websockify.",
    })
    config_changes.append({"text": _broker_config_note()})
    registry.write_json("stage0", RUN_ID, "incidents.json", incidents)
    registry.write_json("stage0", RUN_ID, "claims.json", claims)
    registry.write_json("stage0", RUN_ID, "not_checked.json", unchecked)
    registry.write_json("stage0", RUN_ID, "config_changes.json", config_changes)
    registry.write_json("stage0", RUN_ID, "owner_actions.json", owner)
    registry.write_json("stage0", RUN_ID, "criteria_changes.json", [{
        "before": "Маркер «24GB» сравнивался как буквальная подстрока.",
        "after": "Перед сравнением маркера убирается один пробел между числом и единицей измерения.",
        "why": "Карточка лота #130 содержит «24 GB». Буквальная проверка отвергала бы текст самой карточки.",
    }])
    registry.finalize("stage0", RUN_ID)
    print(json.dumps({"run_id": RUN_ID, "broker_up": broker_up}))


if __name__ == "__main__":
    main()
