"""Tests of the ANDROID bridge (TZ M1 B.1, B.3, B.4, B.5 test part;
mutations D.3-5/6/7 GREEN twins).

The real ADB transport is part B.2 (BLOCKED-EXTERNAL — no dedicated phone
yet); these tests drive the bridge through the AdbTransport protocol with
fakes, which is legitimate without a phone: the B.3 mutex, the B.4
evidence layer and the B.5 screen recognizer are transport-independent.
The screen fixture XMLs are SYNTHETIC references (Ч-6: re-verify on the
real device during B.6).
"""

import base64
import time
import threading
from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from tests.conftest import clean_database

from nextlife.android.bridge import AndroidBridge
from nextlife.android.evidence import validate_evidence
from nextlife.android.errors import (
    AndroidDeviceUnavailableError,
    BridgeDisabledError,
    BridgeEvidenceMissingError,
    BridgeTransportMissingError,
    BrokerAppNotInstalledError,
    BrokerLimitExceededError,
    BrokerMaintenanceError,
    UnknownScreenError,
)
from nextlife.android.screens import (
    ScreenRecognizer,
    make_recognizer,
    parse_hierarchy_ids_and_texts,
)
from nextlife.android.transport import AdbTransport
from nextlife.core.config import (
    AndroidBridgeConfig,
    AndroidMarketplaceConfig,
    HumanPacingConfig,
    load_config,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BridgeLock

FIX = Path(__file__).parent / "fixtures" / "android"
HOME = FIX.joinpath("vinted_home.xml").read_text(encoding="utf-8")
HOME_ROTATED = FIX.joinpath("vinted_home_rotated.xml").read_text(encoding="utf-8")
SEARCH = FIX.joinpath("vinted_search_results.xml").read_text(encoding="utf-8")
SEARCH_POST_SHARE = FIX.joinpath("vinted_search_results_post_share.xml").read_text(encoding="utf-8")
DETAIL = FIX.joinpath("vinted_listing_detail.xml").read_text(encoding="utf-8")
UPDATE_DIALOG = FIX.joinpath("vinted_unknown_update_dialog.xml").read_text(encoding="utf-8")

# 1x1 black pixel PNG (evidence must be a real PNG file)
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeTransport:
    """AdbTransport fake: records call order/timestamps, serves configured
    screens, writes real PNG/XML artifacts."""

    def __init__(
        self,
        *,
        online: bool = True,
        screens: list[str] | None = None,
        app_version: str = "2.0.0",
        screenshot_pause_s: float = 0.0,
        skip_screenshot: bool = False,
    ) -> None:
        self.online = online
        self.screens = list(screens) if screens else [HOME]
        self.screen_idx = 0
        self._version = app_version
        self.screenshot_pause_s = screenshot_pause_s
        self.skip_screenshot = skip_screenshot
        self.events: list[tuple[str, float]] = []
        self.search_queries: list[str] = []

    def _screen(self) -> str:
        return self.screens[min(self.screen_idx, len(self.screens) - 1)]

    def _mark(self, name: str) -> None:
        self.events.append((name, time.monotonic()))

    def begin(self, platform: str = "", priority: str = "background") -> None:
        self._mark("begin")

    def end(self) -> None:
        self._mark("end")

    def ping(self) -> bool:
        return self.online

    def screenshot(self, dest: Path) -> Path:
        self._mark("scr_start")
        if self.screenshot_pause_s:
            time.sleep(self.screenshot_pause_s)
        if not self.skip_screenshot:
            dest.write_bytes(PNG_1X1)
        self._mark("scr_end")
        return dest

    def hierarchy_dump(self, dest: Path) -> Path:
        dest.write_text(self._screen(), encoding="utf-8")
        return dest

    def app_version(self) -> str:
        return self._version

    def open_app(self) -> None:
        self._mark("open_app")

    def search_in_app(self, query: str, search_config: dict[str, str] | None = None) -> str:
        self.search_queries.append(query)
        self.screen_idx += 1
        return self._screen()

    def extract_card_links(
        self,
        first_xml: str,
        search_config: dict[str, str] | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: object = None,
    ) -> tuple[list[str], str]:
        return [], ""

    def open_listing(self, ref: str) -> None:
        self.screen_idx += 1


def make_config(*, enabled: bool = True) -> AndroidBridgeConfig:
    return AndroidBridgeConfig(
        enabled=enabled,
        pacing=HumanPacingConfig(min_pause_seconds=0.0, max_pause_seconds=0.0),
    )


def make_marketplaces() -> dict[str, AndroidMarketplaceConfig]:
    """The broker platform id + version-tied screen fingerprints per
    marketplace (TZ Marketplaces: the broker is the single source of truth
    for the app package + rate limits)."""
    from nextlife.core.config import ScreenFingerprintConfig

    return {
        "vinted": AndroidMarketplaceConfig(
            platform_id="vinted",
            fingerprints=[
                ScreenFingerprintConfig(
                    name="vinted_home",
                    version="2.0.0",
                    resource_ids=["navigation_tab_browse", "homepage_chips_list"],
                    texts=["Domů", "Procházej"],
                ),
                # 2026-10-11: header text scrolls off after the Share flow;
                # recognize by the stable grid/filter ids (see vinted.yaml).
                ScreenFingerprintConfig(
                    name="vinted_search_results",
                    version="2.0.0",
                    resource_ids=["item_grid_recycler_view", "item_box_container", "horizontal_filters_list"],
                    texts=[],
                ),
                ScreenFingerprintConfig(
                    name="vinted_listing_detail",
                    version="2.0.0",
                    resource_ids=["item_screen_content", "item_summary_content", "total_price"],
                    texts=["Koupit", "Nabídnout cenu"],
                ),
            ],
        )
    }


@pytest.fixture(autouse=True)
def _clean_bridge_state() -> Generator[None, None, None]:
    with get_db_session() as session:
        clean_database(session)
    yield


def make_bridge(
    config: AndroidBridgeConfig,
    transport: AdbTransport | None,
    tmp_path: Path,
    session: Session,
) -> AndroidBridge:
    return AndroidBridge(
        config,
        transport,
        session=session,
        artifact_root=tmp_path / "evidence",
        marketplaces=make_marketplaces(),
    )


# --------------------------------------------------------------------- #
# B.1: single implementation + config model validated by the schema
# --------------------------------------------------------------------- #


def test_b1_config_model_validated_by_schema() -> None:
    cfg = load_config().app.android_bridge
    # the old stub config (host/port of a local fake bridge) is gone;
    # the adb node address comes from environment variables (B.2)
    assert not hasattr(cfg, "host") or cfg.adb.host_env
    assert not hasattr(cfg, "port") or cfg.adb.port_env
    assert cfg.adb.host_env == "NEXTLIFE_ADB_HOST"
    assert cfg.adb.port_env == "NEXTLIFE_ADB_PORT"
    # the phone is provided via phonebroker; the bridge is enabled (B6)
    assert cfg.enabled is True
    # TZ Marketplaces: the broker is the single source of truth for the app
    # package + rate limits — the NextLife config no longer carries them
    assert not hasattr(cfg, "daily_caps")
    assert not hasattr(cfg, "apps")
    assert not hasattr(cfg.phone_broker, "platform")
    # schema validation (a list is never coerced to bool in pydantic v2)
    with pytest.raises(ValidationError):
        AndroidBridgeConfig(enabled=[1])  # type: ignore[arg-type]
    ok = AndroidBridgeConfig(enabled=True)
    assert ok.enabled is True


# --------------------------------------------------------------------- #
# TZ Marketplaces: the broker's task statuses (423/403/409/503) map to
# distinct named errors — a task status, NOT a blanket connector error
# --------------------------------------------------------------------- #


def test_broker_status_code_mapping() -> None:
    from nextlife.android.transport import PhoneBrokerTransport

    assert isinstance(
        PhoneBrokerTransport._status_error("screenshot", 423, {"detail": "maintenance"}),
        BrokerMaintenanceError,
    )
    assert isinstance(
        PhoneBrokerTransport._status_error("screenshot", 403, {"detail": "limit"}),
        BrokerLimitExceededError,
    )
    assert isinstance(
        PhoneBrokerTransport._status_error("screenshot", 409, {"detail": "not installed"}),
        BrokerAppNotInstalledError,
    )
    assert isinstance(
        PhoneBrokerTransport._status_error("screenshot", 503, {"detail": "down"}),
        AndroidDeviceUnavailableError,
    )
    # an unknown status is a transport failure, not a task status
    assert isinstance(
        PhoneBrokerTransport._status_error("screenshot", 500, {"detail": "boom"}),
        AndroidDeviceUnavailableError,
    )


# --------------------------------------------------------------------- #
# guards: disabled channel / missing transport / caps (B.2, Ч-7)
# --------------------------------------------------------------------- #


def test_disabled_channel_raises_bridge_disabled(tmp_path: Path) -> None:
    with get_db_session() as session:
        bridge = make_bridge(make_config(enabled=False), FakeTransport(), tmp_path, session)
        with pytest.raises(BridgeDisabledError, match="confirmations 5/6"):
            bridge.health()


def test_missing_transport_honest_error(tmp_path: Path) -> None:
    with get_db_session() as session:
        bridge = make_bridge(make_config(), None, tmp_path, session)
        with pytest.raises(BridgeTransportMissingError, match="BLOCKED-EXTERNAL"):
            bridge.search("vinted", "notebook")


def test_device_offline_blocks(tmp_path: Path) -> None:
    with get_db_session() as session:
        bridge = make_bridge(make_config(), FakeTransport(online=False), tmp_path, session)
        with pytest.raises(AndroidDeviceUnavailableError, match="BLOCKED"):
            bridge.health()


# --------------------------------------------------------------------- #
# B.4: evidence layer — a result without evidence is a FAIL
# --------------------------------------------------------------------- #


def test_health_evidence_png_dump_timestamps(tmp_path: Path) -> None:
    with get_db_session() as session:
        bridge = make_bridge(make_config(), FakeTransport(), tmp_path, session)
        res = bridge.health()
        assert res.ok is True
        ev = res.evidence
        assert len(ev.steps) == 1
        shot, dump = ev.steps[0].screenshot, ev.steps[0].hierarchy_dump
        assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert dump.read_text(encoding="utf-8").lstrip().startswith("<?xml")
        assert ev.finished_at is not None and ev.finished_at >= ev.started_at


def test_search_two_key_steps_recognized_screen_and_version(tmp_path: Path) -> None:
    transport = FakeTransport(screens=[HOME, SEARCH])
    with get_db_session() as session:
        bridge = make_bridge(make_config(), transport, tmp_path, session)
        res = bridge.search("vinted", "notebook")
        assert res.ok is True
        assert transport.search_queries == ["notebook"]
        assert len(res.evidence.steps) == 2  # screenshot on each key step
        for step in res.evidence.steps:
            assert step.screenshot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert res.evidence.app_version == "2.0.0"
        assert res.data["screen"] == "vinted_search_results"


def test_missing_screenshot_is_fail(tmp_path: Path) -> None:
    """Mutation D.3-6 twin: a call without a screenshot must FAIL."""
    transport = FakeTransport(screens=[HOME], skip_screenshot=True)
    with get_db_session() as session:
        bridge = make_bridge(make_config(), transport, tmp_path, session)
        with pytest.raises(BridgeEvidenceMissingError, match="screenshot missing or empty"):
            bridge.health()


def test_missing_app_version_is_fail(tmp_path: Path) -> None:
    transport = FakeTransport(screens=[HOME], app_version="")
    with get_db_session() as session:
        bridge = make_bridge(make_config(), transport, tmp_path, session)
        with pytest.raises(BridgeEvidenceMissingError, match="app_version is missing"):
            bridge.app_state("vinted")


def test_validate_evidence_requires_all_parts(tmp_path: Path) -> None:
    from nextlife.android.evidence import BridgeEvidence, EvidenceStep
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    shot = tmp_path / "s.png"
    shot.write_bytes(PNG_1X1)
    dump = tmp_path / "d.xml"
    dump.write_text("<hierarchy></hierarchy>", encoding="utf-8")
    ok_ev = BridgeEvidence(
        scenario="health",
        started_at=now,
        finished_at=now,
        app_version="1.0",
        steps=[EvidenceStep("step_01", shot, dump)],
    )
    validate_evidence(ok_ev)  # no error
    # broken dump -> FAIL
    dump.write_text("not xml at all", encoding="utf-8")
    with pytest.raises(BridgeEvidenceMissingError, match="not XML"):
        validate_evidence(ok_ev)


# --------------------------------------------------------------------- #
# B.3 property 1: global DB mutex — the android queue is strictly
# sequential (mutation D.3-5 twin)
# --------------------------------------------------------------------- #


def test_two_concurrent_calls_run_sequentially(tmp_path: Path) -> None:
    """Two concurrent bridge calls must not overlap. Uses health() (not
    search) on purpose: health skips the per-marketplace daily-cap counter,
    so the ONLY thing serializing the two calls is the global bridge mutex —
    without it the critical sections overlap and this test is RED
    (mutation D.3-5)."""
    outcomes: dict[str, tuple[bool, list[tuple[str, float]]]] = {}

    def worker(name: str) -> None:
        transport = FakeTransport(screens=[HOME, SEARCH], screenshot_pause_s=0.4)
        with get_db_session() as session:
            bridge = make_bridge(make_config(), transport, tmp_path / name, session)
            res = bridge.health()
            outcomes[name] = (res.ok, transport.events)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    start = time.monotonic()
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    total = time.monotonic() - start

    assert outcomes["a"][0] and outcomes["b"][0]
    a = (outcomes["a"][1][0][1], outcomes["a"][1][-1][1])
    b = (outcomes["b"][1][0][1], outcomes["b"][1][-1][1])
    # no overlap of the two critical sections
    assert not (max(a[0], b[0]) < min(a[1], b[1])), "concurrent sections overlapped — mutex missing?"
    # two 0.4s-pause scenarios serialized take >= 0.8s in total
    assert total >= 0.8


# --------------------------------------------------------------------- #
# B.3 property 3 / B.5: screen recognition on ids/texts (never
# coordinates); unknown screen -> BLOCKED, not continuation
# (mutation D.3-7 twin)
# --------------------------------------------------------------------- #


def test_recognizer_knows_the_three_reference_screens() -> None:
    fps = make_marketplaces()["vinted"].fingerprints
    from nextlife.android.screens import ScreenFingerprint

    rec = make_recognizer(
        [ScreenFingerprint(name=f.name, resource_ids=tuple(f.resource_ids), texts=tuple(f.texts)) for f in fps]
    )
    assert rec.recognize(HOME) == "vinted_home"
    assert rec.recognize(SEARCH) == "vinted_search_results"
    assert rec.recognize(DETAIL) == "vinted_listing_detail"
    assert rec.recognize(UPDATE_DIALOG) is None  # unknown -> None


def test_vinted_search_results_fingerprint_survives_share_flow() -> None:
    """2026-10-11 regression (live dump, app 26.39.0): after the in-app Share
    flow (extract_card_links) the results header text «Výsledky vyhledávání»
    scrolls off, so a text-requiring fingerprint no longer matches the live
    results screen -> UnknownScreenError. The config fingerprint must
    recognize the post-share dump (structure ids only), and a text-requiring
    variant must fail (mutation check)."""
    from nextlife.android.screens import ScreenFingerprint

    fps = make_marketplaces()["vinted"].fingerprints
    rec = make_recognizer(
        [ScreenFingerprint(name=f.name, resource_ids=tuple(f.resource_ids), texts=tuple(f.texts)) for f in fps]
    )
    # The live post-share dump (header scrolled off) is the results screen.
    assert rec.recognize(SEARCH_POST_SHARE) == "vinted_search_results"
    # Mutation: a fingerprint that still requires the (scrolled-off) header
    # text must NOT match the post-share dump.
    old_fp = ScreenFingerprint(
        name="vinted_search_results",
        resource_ids=("item_grid_recycler_view", "item_box_container"),
        texts=("Výsledky vyhledávání",),
    )
    ids, texts = parse_hierarchy_ids_and_texts(SEARCH_POST_SHARE)
    assert old_fp.matches(ids, texts) is False


def test_recognizer_ignores_coordinates() -> None:
    """B.3 property 5: the same screen with rotated/shifted bounds is
    still recognized (ids/texts only, no coordinate binding)."""
    fps = make_marketplaces()["vinted"].fingerprints
    from nextlife.android.screens import ScreenFingerprint

    rec = make_recognizer(
        [ScreenFingerprint(name=f.name, resource_ids=tuple(f.resource_ids), texts=tuple(f.texts)) for f in fps]
    )
    assert rec.recognize(HOME) == rec.recognize(HOME_ROTATED) == "vinted_home"


def test_unknown_screen_blocked_not_continued(tmp_path: Path) -> None:
    """Mutation D.3-7 twin: an update dialog on the final step must give
    BLOCKED (UnknownScreenError), never a continued/succeeded scenario."""
    transport = FakeTransport(screens=[HOME, UPDATE_DIALOG])
    with get_db_session() as session:
        bridge = make_bridge(make_config(), transport, tmp_path, session)
        with pytest.raises(UnknownScreenError, match="BLOCKED"):
            bridge.search("vinted", "notebook")


def test_unexpected_known_screen_blocked(tmp_path: Path) -> None:
    """A KNOWN screen that is not the one the scenario expects (e.g. the
    home screen where results are expected) is also BLOCKED."""
    transport = FakeTransport(screens=[HOME, HOME])  # search 'results' = home
    with get_db_session() as session:
        bridge = make_bridge(make_config(), transport, tmp_path, session)
        with pytest.raises(UnknownScreenError, match="unexpected screen"):
            bridge.search("vinted", "notebook")
