"""Fetch real ECB exchange rates and produce E2 acceptance evidence artifacts."""

import json
from pathlib import Path

import httpx

from nextlife.core.config import load_config
from nextlife.money.money import parse_ecb_xml, refresh_fx_rates
from nextlife.storage.db import get_db_session
from nextlife.storage.models import FxRate

ROOT_DIR = Path(__file__).parent.parent
E2_EVIDENCE_DIR = ROOT_DIR / "acceptance" / "evidence" / "E2"


def fetch_and_record_ecb() -> None:
    E2_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    feed_url = cfg.app.fx.source_url

    print(f"1. Запрос к живому фиду ЕЦБ: {feed_url}")
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(feed_url)
        resp.raise_for_status()
        raw_xml = resp.content

    xml_path = E2_EVIDENCE_DIR / "fx_ecb_raw.xml"
    xml_path.write_bytes(raw_xml)
    print(f"   Сырой XML сохранён в {xml_path} ({len(raw_xml)} байт)")

    parsed = parse_ecb_xml(raw_xml)
    if not parsed:
        raise ValueError("В XML-ответе ЕЦБ не найдено курсов валют")
    feed_date = parsed[0]["rate_date"]
    print(f"   Дата фида: {feed_date}, найдено валют: {len(parsed)}")

    print("2. Сохранение курсов в базу данных...")
    with get_db_session() as session:
        # We also call refresh_fx_rates to ensure the official service code works
        count = refresh_fx_rates(session, feed_url=feed_url)
        session.commit()
        print(f"   Успешно обновлено/вставлено записей: {count}")

        # Retrieve all rates for this date
        rows = (
            session.query(FxRate)
            .filter(FxRate.rate_date == feed_date)
            .order_by(FxRate.currency)
            .all()
        )
        print(f"   Записей в БД на дату {feed_date}: {len(rows)}")

        rows_data = [
            {
                "currency": r.currency,
                "rate_date": r.rate_date.isoformat(),
                "rate_to_base": str(r.rate_to_base),
                "source": r.source,
                "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
            }
            for r in rows
        ]

    json_path = E2_EVIDENCE_DIR / "fx_rows.json"
    json_path.write_text(json.dumps(rows_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   Строки БД сохранены в {json_path} ({len(rows_data)} записей)")
    print("Готово! Все артефакты этапа Э2-R успешно сформированы.")


if __name__ == "__main__":
    fetch_and_record_ecb()
