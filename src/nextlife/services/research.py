"""Market research and comparable items analysis service (section 9.2, Ш2)."""

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.connectors.base import RawListing
from nextlife.connectors.registry import get_connector, sync_marketplaces
from nextlife.core.config import ConfigBundle
from nextlife.core.exceptions import NextLifeError
from nextlife.core.research_texts import generate_research_texts
from nextlife.money.money import UnknownCurrencyError, convert_price, format_money
from nextlife.storage.models import Item, ResearchResult, ResearchRun


class ResearchError(NextLifeError):
    """Raised when research run fails."""


def calculate_percentile(sorted_vals: list[Decimal], p: float) -> Decimal:
    """Calculate p-th percentile (0.0 to 1.0) using linear interpolation."""
    if not sorted_vals:
        return Decimal("0.00")
    if len(sorted_vals) == 1:
        return sorted_vals[0]

    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[-1]
    d = Decimal(str(k - f))
    return (sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f])).quantize(Decimal("0.01"))


def filter_outliers_iqr(prices: list[Decimal]) -> list[Decimal]:
    """Filter out price outliers using Interquartile Range (IQR)."""
    if len(prices) < 4:
        return sorted(prices)

    sorted_p = sorted(prices)
    q1 = calculate_percentile(sorted_p, 0.25)
    q3 = calculate_percentile(sorted_p, 0.75)
    iqr = q3 - q1

    lower_bound = q1 - Decimal("1.5") * iqr
    upper_bound = q3 + Decimal("1.5") * iqr

    return [p for p in sorted_p if lower_bound <= p <= upper_bound]


def calculate_price_stats(prices: list[Decimal], min_comparables: int = 5) -> dict[str, Any]:
    """Calculate IQR filtered price stats or return insufficient data reason (Rule П-1.3)."""
    filtered = filter_outliers_iqr(prices)
    if len(filtered) < min_comparables:
        return {
            "sufficient_data": False,
            "reason": f"Выборка недостаточна для рыночной оценки: найдено {len(filtered)} (требуется {min_comparables}).",
            "count": len(filtered),
        }

    sorted_p = sorted(filtered)
    return {
        "sufficient_data": True,
        "count": len(sorted_p),
        "min": str(sorted_p[0]),
        "q25": str(calculate_percentile(sorted_p, 0.25)),
        "median": str(calculate_percentile(sorted_p, 0.50)),
        "q75": str(calculate_percentile(sorted_p, 0.75)),
        "max": str(sorted_p[-1]),
    }


def extract_search_candidates(title: str) -> list[str]:
    """Генерация поисковых кандидатов (от более точных к кратким) для поиска на досках объявлений."""
    title = title.strip()
    if not title:
        return []
    candidates = [title]

    cleaned = re.sub(r"\b\d+\s*(?:GB|TB|MB|G|M)\b", "", title, flags=re.IGNORECASE).strip()
    noise = r"\b(?:TUF|Gaming|OC|Edition|Series|Ultra|Strix|Pulse|Nitro|Windforce|Ventus)\b"
    core = re.sub(noise, "", cleaned, flags=re.IGNORECASE)
    core = re.sub(r"\s+", " ", core).strip()
    if core and core not in candidates:
        candidates.append(core)

    model_patterns = [
        r"(?:Radeon\s+)?RX\s*\d{3,4}(?:\s*(?:XTX|XT|GRE))?",
        r"(?:GeForce\s+)?RTX\s*\d{3,4}(?:\s*(?:Ti|SUPER))?",
        r"(?:GeForce\s+)?GTX\s*\d{3,4}(?:\s*Ti)?",
        r"(?:Core\s+)?i[3579][-\s]\d{4,5}[A-Z]*",
        r"Ryzen\s+[3579]\s+\d{4,5}[A-Z]*",
        r"iPhone\s+\d{1,2}(?:\s*(?:Pro\s*Max|Pro|Plus|Mini))?",
        r"PlayStation\s*\d|PS[45]",
    ]
    for pat in model_patterns:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            c = m.group(0).strip()
            if c and c not in candidates:
                candidates.append(c)
            unbranded = re.sub(r"^(?:Radeon|GeForce|Core)\s+", "", c, flags=re.IGNORECASE).strip()
            if unbranded and unbranded not in candidates:
                candidates.append(unbranded)

    words = [w for w in title.split() if len(w) > 1]
    if len(words) > 3:
        short = " ".join(words[-3:])
        if short not in candidates:
            candidates.append(short)

    return candidates


def run_research(
    session: Session,
    item_code: str,
    query: str,
    config: ConfigBundle,
    raw_listings_override: list[tuple[str, RawListing]] | None = None,
) -> str:
    """Run market research and save raw + converted results to DB."""
    sync_marketplaces(session, config)

    run_id = str(uuid.uuid4())
    run = ResearchRun(
        id=run_id,
        item_code=item_code,
        started_at=datetime.now(UTC),
        finished_at=None,
        status="RUNNING",
        marketplaces={"queried": list(config.marketplaces.keys())},
        error={},
    )
    session.add(run)
    session.flush()

    raw_items: list[tuple[str, RawListing]] = []
    effective_query = query.strip()
    if raw_listings_override is not None:
        raw_items = raw_listings_override
    else:
        # Сначала ищем по точному запросу; при 0 результатов пробуем кандидаты модели
        candidates = [query.strip()]
        for c in extract_search_candidates(query):
            if c not in candidates:
                candidates.append(c)

        for candidate_q in candidates:
            cand_items = []
            for mp_key, mp_cfg in config.marketplaces.items():
                if mp_cfg.access_mode != "DIRECT":
                    continue
                try:
                    connector = get_connector(mp_key, config)
                    items = connector.search(candidate_q)
                    for item in items:
                        cand_items.append((mp_key, item))
                except Exception as e:
                    run.error[mp_key] = str(e)
            if cand_items:
                raw_items = cand_items
                effective_query = candidate_q
                break

    today = date.today()
    now_utc = datetime.now(UTC)

    for mp_key, raw_item in raw_items:
        try:
            triple = convert_price(
                session=session,
                amount=raw_item.price,
                currency=raw_item.currency,
                target_date=today,
                base_currency=config.app.base_currency,
            )
            price_base = triple.amount_base
            fx_date = triple.rate_date
        except UnknownCurrencyError:
            # Rule 7.6: currency_unknown -> skip aggregate inclusion
            continue

        res_row = ResearchResult(
            id=str(uuid.uuid4()),
            run_id=run_id,
            marketplace_key=mp_key,
            external_url=raw_item.external_url,
            title=raw_item.title,
            price=raw_item.price,
            currency=raw_item.currency,
            price_base=price_base,
            fx_rate_date=fx_date,
            condition_text=raw_item.condition_text,
            posted_at=raw_item.posted_at,
            raw=raw_item.raw,
            captured_at=now_utc,
        )
        session.add(res_row)

    run.marketplaces = {
        "queried": list(config.marketplaces.keys()),
        "query": effective_query,
        "original_query": query.strip(),
    }
    run.finished_at = datetime.now(UTC)
    run.status = "FINISHED"
    session.flush()

    # Автоматический расчёт статистики цен и сохранение в Item для витрины Склада
    item_row = session.get(Item, item_code)
    if item_row:
        curr = item_row.target_currency or "CZK"
        own_prices = sorted(
            r.price for r in session.scalars(
                select(ResearchResult).where(
                    ResearchResult.run_id == run_id,
                    ResearchResult.currency == curr,
                )
            ).all()
        )
        if not own_prices:
            own_prices = sorted(
                r.price for r in session.scalars(
                    select(ResearchResult).where(ResearchResult.run_id == run_id)
                ).all()
            )
        if own_prices:
            stats = calculate_price_stats(own_prices, min_comparables=min(len(own_prices), 3))
            if stats.get("median"):
                item_row.price_estimate_median = Decimal(str(stats["median"]))
                item_row.price_estimate_low = Decimal(str(stats.get("q25") or stats.get("min") or stats["median"]))
                item_row.price_estimate_high = Decimal(str(stats.get("q75") or stats.get("max") or stats["median"]))
                item_row.price_estimate_currency = curr
                item_row.price_estimate_at = datetime.now(UTC)
                session.flush()

    return run_id


def build_research_report(
    session: Session,
    run_id: str,
    config: ConfigBundle,
) -> dict[str, Any]:
    """Generate structured research report according to section 9.2, Ш2."""
    stmt = select(ResearchResult).where(ResearchResult.run_id == run_id)
    results = session.execute(stmt).scalars().all()

    counts_by_marketplace: dict[str, int] = {}
    for r in results:
        counts_by_marketplace[r.marketplace_key] = (
            counts_by_marketplace.get(r.marketplace_key, 0) + 1
        )

    closest_links = [
        {
            "marketplace": r.marketplace_key,
            "title": r.title,
            "url": r.external_url,
            "price_base": float(r.price_base),
            "currency_base": config.app.base_currency,
        }
        for r in results[:5]
    ]

    prices_base = [r.price_base for r in results]
    min_comp = config.app.min_comparables
    stats = calculate_price_stats(prices_base, min_comparables=min_comp)

    report: dict[str, Any] = {
        "run_id": run_id,
        "total_found": len(results),
        "counts_by_marketplace": counts_by_marketplace,
        "closest_links": closest_links,
        "insufficient_sample": not stats["sufficient_data"],
        "message": stats.get("reason"),
        "price_range": None,
    }

    if stats["sufficient_data"]:
        report["price_range"] = {
            "base_currency": config.app.base_currency,
            "min": float(stats["min"]),
            "q25": float(stats["q25"]),
            "median": float(stats["median"]),
            "q75": float(stats["q75"]),
            "max": float(stats["max"]),
        }
    return report


def build_research_panel(
    session: Session, item: Item, config: ConfigBundle
) -> dict[str, Any] | None:
    """Данные панели разведки для UI (TZ-NextLife-UI-N2 §4 V5, P-R07/P-R08).

    Последний run предмета -> отчёт для экрана. Диапазон — в валюте предмета
    (никогда базовая валюта — P-E01), только при выборке не меньше
    min_comparables, с числом аналогов и ссылками-источниками; иначе —
    явное сообщение. Диапазон без источников не выводится (П-1.3).
    """
    run = (
        session.execute(
            select(ResearchRun)
            .where(ResearchRun.item_code == item.code)
            .order_by(ResearchRun.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if run is None:
        return None

    results = (
        session.execute(
            select(ResearchResult)
            .where(ResearchResult.run_id == run.id)
            .order_by(ResearchResult.captured_at, ResearchResult.title)
        )
        .scalars()
        .all()
    )
    # Аналоги в валюте предмета — база диапазона (П-1.3/П-1.4).
    target_curr = item.target_currency or "CZK"
    own = [r for r in results if r.currency == target_curr]
    if not own and results:
        own = list(results)

    prices = sorted(r.price for r in own)
    min_comp = getattr(config.app, "min_comparables", 5)
    min_comp = min_comp if isinstance(min_comp, int) else 5
    stats = calculate_price_stats(prices, min_comparables=min_comp)

    panel: dict[str, Any] = {
        "run_id": run.id,
        "query": (run.marketplaces or {}).get("query") or (run.marketplaces or {}).get("original_query") or item.title,
        "status": run.status,
        "total_found": len(own),
        "range": None,
        "message": None,
        "links": [
            {
                "marketplace": r.marketplace_key,
                "title": r.title,
                "url": r.external_url,
                "price": str(r.price),
                "currency": r.currency,
            }
            for r in own[:10]
        ],
        "variants": (
            generate_research_texts(
                item.title,
                len(own),
                prices[0] if prices else None,
                prices[-1] if prices else None,
                currency=item.target_currency or "CZK",
            )
            if own
            else []
        ),
    }
    if stats["sufficient_data"]:
        panel["range"] = {
            "min": format_money(stats["min"], item.target_currency),
            "max": format_money(stats["max"], item.target_currency),
        }
    else:
        panel["message"] = stats["reason"]
    return panel
