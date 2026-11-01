from pathlib import Path

from nextlife.connectors.registry import get_connector
from nextlife.core.config import load_config

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_real_recorded_fixtures_bazos():
    config = load_config()
    conn = get_connector("bazos_cz", config)
    fixture_file = FIXTURES_DIR / "bazos_sample.html"
    assert fixture_file.exists()

    listings = conn.search_with_html(fixture_file.read_text(encoding="utf-8"))
    assert len(listings) >= 1
    for item in listings:
        assert item.price > 0
        assert item.currency == "CZK"
        assert item.title
        assert item.external_url.startswith("http")


def test_real_recorded_fixtures_sbazar():
    config = load_config()
    conn = get_connector("sbazar_cz", config)
    fixture_file = FIXTURES_DIR / "sbazar_sample.html"
    assert fixture_file.exists()

    listings = conn.search_with_html(fixture_file.read_text(encoding="utf-8"))
    assert len(listings) >= 1
    for item in listings:
        assert item.price > 0
        assert item.currency == "CZK"
        assert item.title
        assert item.external_url.startswith("http")


def test_real_recorded_fixtures_aukro():
    config = load_config()
    conn = get_connector("aukro_cz", config)
    fixture_file = FIXTURES_DIR / "aukro_sample.json"  # JSON API response (A.3)
    assert fixture_file.exists()

    listings = conn.search_with_json(fixture_file.read_text(encoding="utf-8"))
    assert len(listings) >= 1
    for item in listings:
        assert item.price > 0
        assert item.currency == "CZK"
        assert item.title
        assert item.external_url.startswith("http")


def test_real_recorded_fixtures_hyperinzerce():
    config = load_config()
    conn = get_connector("hyperinzerce_cz", config)
    fixture_file = FIXTURES_DIR / "hyperinzerce_sample.html"
    assert fixture_file.exists()

    listings = conn.search_with_html(fixture_file.read_text(encoding="utf-8"))
    assert len(listings) >= 1
    for item in listings:
        assert item.price > 0
        assert item.currency == "CZK"
        assert item.title
        assert item.external_url.startswith("http")


def test_real_recorded_fixtures_annonce():
    config = load_config()
    conn = get_connector("annonce_cz", config)
    fixture_file = FIXTURES_DIR / "annonce_sample.html"
    assert fixture_file.exists()

    listings = conn.search_with_html(fixture_file.read_text(encoding="utf-8"))
    assert len(listings) >= 1
    for item in listings:
        assert item.price > 0
        assert item.currency == "CZK"
        assert item.title
        assert item.external_url.startswith("http")


def test_real_recorded_fixtures_trhknih():
    config = load_config()
    conn = get_connector("trhknih_cz", config)
    fixture_file = FIXTURES_DIR / "trhknih_sample.html"
    assert fixture_file.exists()

    listings = conn.search_with_html(fixture_file.read_text(encoding="utf-8"))
    assert len(listings) >= 1
    for item in listings:
        assert item.price > 0
        assert item.currency == "CZK"
        assert item.title
        assert item.external_url.startswith("http")
