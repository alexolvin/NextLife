"""Tests for Stage E3 (Коннекторы обычных площадок и разведка)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.connectors.base import ConnectorConfigError, RawListing
from nextlife.connectors.registry import get_connector
from nextlife.core.config import MarketplaceConfig, load_config
from nextlife.services.research import (
    build_research_report,
    filter_outliers_iqr,
    run_research,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import FxRate, Item


def test_connectors_parse_fixtures() -> None:
    config = load_config()
    fixtures_dir = Path(__file__).parent / "fixtures"

    # 1. Bazos
    # Fixture: live search probe 2026-10-04 (.a/reports/canaries/2026-10-04/
    # bazos_cz_005939.html), 20 cards with dates from the card markup.
    bazos_html = (fixtures_dir / "bazos_sample.html").read_text(encoding="utf-8")
    bazos_conn = get_connector("bazos_cz", config)
    bazos_listings = bazos_conn.search_with_html(bazos_html)
    assert len(bazos_listings) == 20
    assert bazos_listings[0].price == Decimal("22900")
    assert bazos_listings[0].currency == "CZK"
    assert "Lenovo T14s gen 5" in bazos_listings[0].title
    assert "bazos.cz/inzerat/224600608" in bazos_listings[0].external_url
    assert all(l.posted_at is not None for l in bazos_listings)

    # 2. Sbazar (fixture captured 2026-10-03 from live /hledej/ markup)
    sbazar_html = (fixtures_dir / "sbazar_sample.html").read_text(encoding="utf-8")
    sbazar_conn = get_connector("sbazar_cz", config)
    sbazar_listings = sbazar_conn.search_with_html(sbazar_html)
    assert len(sbazar_listings) == 2
    assert sbazar_listings[0].price == Decimal("10999")
    assert sbazar_listings[0].currency == "CZK"
    assert "sbazar.cz/inzerat/233911845" in sbazar_listings[0].external_url

    # 3. Aukro — live JSON API response recorded 2026-10-04 (A.3 repair:
    # the HTML endpoint serves a JS SPA shell; search uses the site-internal
    # JSON API, .a/reports/m1/probes/2026-10-04/aukro_cz_search_api_probe.json)
    aukro_json = (fixtures_dir / "aukro_sample.json").read_text(encoding="utf-8")
    aukro_conn = get_connector("aukro_cz", config)
    aukro_listings = aukro_conn.search_with_json(aukro_json)
    assert len(aukro_listings) == 20
    assert aukro_listings[0].price == Decimal("4501")
    assert aukro_listings[0].currency == "CZK"
    assert aukro_listings[0].external_url.startswith("https://aukro.cz/")


def test_outlier_filtering_iqr() -> None:
    # Cluster around 8000-10000 with extreme outliers 100 and 100000
    prices = [
        Decimal("100"),
        Decimal("8200"),
        Decimal("8500"),
        Decimal("8900"),
        Decimal("9100"),
        Decimal("9500"),
        Decimal("9800"),
        Decimal("10000"),
        Decimal("100000"),
    ]
    filtered = filter_outliers_iqr(prices)
    assert Decimal("100") not in filtered
    assert Decimal("100000") not in filtered
    assert len(filtered) == 7


def test_missing_selector_raises_clear_error() -> None:
    config = load_config()
    raw_mp = config.marketplaces["bazos_cz"].model_dump()
    # Remove price selector
    raw_mp["selectors"]["price"] = ""

    bad_mp_cfg = MarketplaceConfig.model_validate(raw_mp)
    # Creating connector must fail with clear error naming the selector
    with pytest.raises(ConnectorConfigError) as exc_info:
        from nextlife.connectors.bazos import BazosConnector

        BazosConnector(bad_mp_cfg)

    err = str(exc_info.value)
    assert "Missing required selector 'price'" in err
    assert "bazos_cz" in err

    # Sbazar and Aukro remain completely functional
    sbazar_conn = get_connector("sbazar_cz", config)
    assert sbazar_conn.key == "sbazar_cz"


def test_research_insufficient_sample_reports_no_range() -> None:
    config = load_config()
    item_code = "777"

    with get_db_session() as session:
        # Seed FX rate
        fx = FxRate(
            rate_date=date.today(),
            currency="CZK",
            rate_to_base=Decimal("25.0"),
            source="ECB",
            fetched_at=datetime.now(UTC),
        )
        session.merge(fx)

        # Seed Item
        item = Item(
            code=item_code,
            kind="SELL",
            title="Test Item",
            category_key="general",
            attrs={},
            condition_key="used",
            defects={},
            status="DRAFT",
            target_price=Decimal("100"),
            target_currency="EUR",
        )
        session.merge(item)
        session.flush()

        # Provide only 2 listings (less than min_comparables = 5)
        raw_override = [
            (
                "bazos_cz",
                RawListing(
                    title="A",
                    price=Decimal("2500"),
                    currency="CZK",
                    external_url="https://bazos.cz/1",
                    condition_text=None,
                    posted_at=None,
                    raw={},
                ),
            ),
            (
                "sbazar_cz",
                RawListing(
                    title="B",
                    price=Decimal("2750"),
                    currency="CZK",
                    external_url="https://sbazar.cz/2",
                    condition_text=None,
                    posted_at=None,
                    raw={},
                ),
            ),
        ]

        run_id = run_research(
            session=session,
            item_code=item_code,
            query="Test",
            config=config,
            raw_listings_override=raw_override,
        )

        report = build_research_report(session, run_id, config)
        assert report["insufficient_sample"] is True
        assert report["price_range"] is None
        assert "Выборка недостаточна" in report["message"]

        session.rollback()


def test_research_sufficient_sample_calculates_range() -> None:
    config = load_config()
    item_code = "778"

    with get_db_session() as session:
        fx = FxRate(
            rate_date=date.today(),
            currency="CZK",
            rate_to_base=Decimal("25.0"),
            source="ECB",
            fetched_at=datetime.now(UTC),
        )
        session.merge(fx)

        item = Item(
            code=item_code,
            kind="SELL",
            title="Camera",
            category_key="electronics",
            attrs={},
            condition_key="used",
            defects={},
            status="DRAFT",
            target_price=Decimal("100"),
            target_currency="EUR",
        )
        session.merge(item)
        session.flush()

        # Provide 6 listings (>= min_comparables = 5)
        raw_override = [
            (
                "bazos_cz",
                RawListing("A", Decimal("2500"), "CZK", "https://bazos.cz/1", None, None, {}),
            ),  # 100 EUR
            (
                "bazos_cz",
                RawListing("B", Decimal("3000"), "CZK", "https://bazos.cz/2", None, None, {}),
            ),  # 120 EUR
            (
                "sbazar_cz",
                RawListing("C", Decimal("3250"), "CZK", "https://sbazar.cz/3", None, None, {}),
            ),  # 130 EUR
            (
                "sbazar_cz",
                RawListing("D", Decimal("3500"), "CZK", "https://sbazar.cz/4", None, None, {}),
            ),  # 140 EUR
            (
                "aukro_cz",
                RawListing("E", Decimal("3750"), "CZK", "https://aukro.cz/5", None, None, {}),
            ),  # 150 EUR
            (
                "aukro_cz",
                RawListing("F", Decimal("4000"), "CZK", "https://aukro.cz/6", None, None, {}),
            ),  # 160 EUR
        ]

        run_id = run_research(
            session=session,
            item_code=item_code,
            query="Camera",
            config=config,
            raw_listings_override=raw_override,
        )

        report = build_research_report(session, run_id, config)
        assert report["insufficient_sample"] is False
        assert report["price_range"] is not None
        pr = report["price_range"]
        assert pr["base_currency"] == "EUR"
        assert pr["min"] == 100.0
        assert pr["max"] == 160.0
        assert 100.0 <= pr["median"] <= 160.0
        assert len(report["closest_links"]) <= 5

        session.rollback()
