"""Fetch live market research evidence for Stage E3-R."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from nextlife.core.config import load_config
from nextlife.services.research import run_research
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ResearchResult, ResearchRun

ROOT_DIR = Path(__file__).parent.parent
E3R_EVIDENCE_DIR = ROOT_DIR / "acceptance" / "evidence" / "E3-R"


def fetch_and_record_research() -> None:
    E3R_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()

    query = "thinkpad"
    bazos_url = f"{config.marketplaces['bazos_cz'].base_url.rstrip('/')}/search.php?hledat={query}"

    print(f"1. Выполнение живого поискового запроса к Bazoš: {bazos_url}")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/122.0.0.0 Safari/537.36"
        )
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=20.0) as client:
        resp = client.get(bazos_url)
        resp.raise_for_status()
        raw_html = resp.text

    # Save raw evidence
    html_path = E3R_EVIDENCE_DIR / "raw_search_bazos.html"
    html_path.write_text(raw_html, encoding="utf-8")
    print(f"   Сырой HTML поиска сохранён в {html_path} ({len(raw_html)} байт)")

    print("2. Создание тестового предмета и запуск run_research...")
    item_code = "777"
    with get_db_session() as session:
        # Ensure Item exists
        item = session.query(Item).filter(Item.code == item_code).first()
        if not item:
            item = Item(
                code=item_code,
                kind="SELL",
                title="Lenovo ThinkPad for Research Evidence",
                category_key="electronics",
                attrs={},
                condition_key="used",
                defects={},
                status="DRAFT",
            )
            session.add(item)
            session.flush()

        run_id = run_research(session, item_code=item_code, query=query, config=config)
        session.commit()
        print(f"   Исследование завершено. research_run.id = {run_id}")

        run_row = session.query(ResearchRun).filter(ResearchRun.id == run_id).first()
        results = session.query(ResearchResult).filter(ResearchResult.run_id == run_id).all()
        print(f"   Найдено и сохранено результатов в БД: {len(results)}")

        run_evidence = {
            "research_run_id": run_id,
            "item_code": item_code,
            "query": query,
            "started_at": run_row.started_at.isoformat()
            if run_row and run_row.started_at
            else None,
            "finished_at": run_row.finished_at.isoformat()
            if run_row and run_row.finished_at
            else None,
            "status": run_row.status if run_row else None,
            "results_count": len(results),
            "sample_results": [
                {
                    "marketplace": r.marketplace_key,
                    "title": r.title,
                    "price_czk": str(r.price),
                    "price_base_eur": str(r.price_base),
                    "fx_rate_date": r.fx_rate_date.isoformat() if r.fx_rate_date else None,
                    "url": r.external_url,
                }
                for r in results[:5]
            ],
            "recorded_at": datetime.now(UTC).isoformat(),
        }

    run_meta_path = E3R_EVIDENCE_DIR / "research_run.json"
    run_meta_path.write_text(
        json.dumps(run_evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"   Метаданные прогона сохранены в {run_meta_path}")
    print("Готово! Все артефакты этапа Э3-R успешно сформированы.")


if __name__ == "__main__":
    fetch_and_record_research()
