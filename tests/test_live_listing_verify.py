"""Verifier tests on a saved real Bazoš listing page.

The HTML and the JPEG were fetched from a public listing
(pc.bazos.cz inzerat 225103106). They are not a synthetic page and they
are not lot #130.
"""

from pathlib import Path

from nextlife.verify.evidence import EvidenceExistsError, EvidenceRegistry
from nextlife.verify.live_listing import (
    LiveListingVerifier,
    compute_phash,
    decide_status,
    normalize_whitespace,
    parse_listing_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "live_pages" / "bazos_listing.html"
PHOTO = Path(__file__).parent / "fixtures" / "live_pages" / "bazos_listing_photo.jpg"
PAGE_URL = "https://pc.bazos.cz/inzerat/225103106/herni-pc-i9-13900k-rx-7900-xtx-24gb-48gb-ddr5.php"


def _parsed():
    return parse_listing_html("bazos_cz", FIXTURE.read_text(encoding="utf-8"), PAGE_URL)


def test_normalize_whitespace():
    assert normalize_whitespace("  hello   world \n test  ") == "hello world test"


def test_real_bazos_page_parses_title_price_location_and_photo():
    parsed = _parsed()
    assert "RX 7900 XTX" in parsed.title
    assert "24GB" in parsed.title
    assert "49 900" in parsed.price_text
    assert "Kč" in parsed.price_text
    assert "Teplice" in parsed.location
    assert parsed.description
    assert any("225103106.jpg" in url for url in parsed.image_urls)
    assert not any("/img/1t/" in url or "/img/106t/" in url for url in parsed.image_urls)


def test_real_photo_matches_itself():
    digest = compute_phash(PHOTO)
    assert digest - digest == 0
    assert PHOTO.stat().st_size > 1000


def test_price_on_real_page_rejects_lot_130_price():
    parsed = _parsed()
    result = decide_status(
        platform="bazos_cz",
        public_url=PAGE_URL,
        external_id="225103106",
        parsed=parsed,
        expected_title=parsed.title,
        expected_price=26000,
        expected_currency="CZK",
        expected_description=parsed.description,
        expected_markers=["RX 7900 XTX"],
        expected_location_fragments=["Teplice"],
        photos_found=1,
        photos_matched=1,
        unmatched_photos=0,
        expected_photo_count=1,
        account_live_count=1,
    )
    assert result.status == "FAILED"
    assert any("цена" in reason for reason in result.failure_reasons)


def test_real_page_fields_pass_only_when_every_check_matches():
    parsed = _parsed()
    result = decide_status(
        platform="bazos_cz",
        public_url=PAGE_URL,
        external_id="225103106",
        parsed=parsed,
        expected_title=parsed.title,
        expected_price="49900",
        expected_currency="CZK",
        expected_description=parsed.description,
        expected_markers=["RX 7900 XTX", "24GB"],
        expected_location_fragments=["Teplice"],
        photos_found=1,
        photos_matched=1,
        unmatched_photos=0,
        expected_photo_count=1,
        account_live_count=1,
    )
    assert result.status == "LIVE_VERIFIED"
    assert result.failure_reasons == []


def test_marker_space_before_unit_still_matches():
    parsed = _parsed()
    parsed.description = parsed.description + " 24 GB"
    parsed.body_text = parsed.description
    result = decide_status(
        platform="bazos_cz",
        public_url=PAGE_URL,
        external_id="225103106",
        parsed=parsed,
        expected_title=parsed.title,
        expected_price="49900",
        expected_currency="CZK",
        expected_description=parsed.description,
        expected_markers=["24GB"],
        expected_location_fragments=["Teplice"],
        photos_found=1,
        photos_matched=1,
        unmatched_photos=0,
        expected_photo_count=1,
        account_live_count=1,
    )
    assert result.status == "LIVE_VERIFIED"


def test_anonymous_block_is_not_a_live_listing():
    result = decide_status(
        platform="bazos_cz",
        public_url=PAGE_URL,
        external_id="225103106",
        parsed=_parsed(),
        expected_title="ignored",
        expected_price=1,
        expected_currency="CZK",
        expected_description="",
        expected_markers=[],
        expected_location_fragments=[],
        photos_found=0,
        photos_matched=0,
        unmatched_photos=0,
        expected_photo_count=0,
        account_live_count=1,
        anonymous_blocked=True,
        http_status=403,
    )
    assert result.status == "PENDING_OWNER_CHECK"


def test_evidence_registry_is_append_only(tmp_path: Path):
    registry = EvidenceRegistry(tmp_path)
    registry.write_text("bazos_cz", "run", "note.txt", "one")
    try:
        registry.write_text("bazos_cz", "run", "note.txt", "two")
        raised = False
    except EvidenceExistsError:
        raised = True
    assert raised
    manifest = registry.finalize("bazos_cz", "run")
    text = manifest.read_text(encoding="utf-8")
    assert "note.txt" in text
    assert "manifest.json" not in text


def test_verifier_class_does_not_import_connectors():
    source = Path(LiveListingVerifier.__module__.replace(".", "/") + ".py")
    # The class lives in nextlife.verify.live_listing.
    text = (Path(__file__).resolve().parents[1] / "src" / "nextlife" / "verify" / "live_listing.py").read_text(encoding="utf-8")
    assert "nextlife.connectors" not in text
    assert source.name == "live_listing.py"
