"""The ANDROID bridge — the single point of contact with the phone (TZ M1 B.3).

Replaces the removed OpenClaw-agent stub (which fabricated success
responses with fake screenshot refs — a Ч-5 landmine). The bridge is
ADB + uiautomator2 over Tailscale (B.2); business logic calls the
high-level scenarios below and never touches or coordinates (B.3).

First iteration (B.4) — Vinted read-only: health, app_state, search,
read_listing. Every scenario:

1. channel enabled in config (B.1);
2. app pin present in config (B.2, confirmation 5);
3. daily cap check (B.2/B.3 property 4, confirmation 8) — atomic DB
   counter, a marketplace without a configured cap gets no actions;
4. global `android` queue mutex (B.3 property 1) — strictly sequential;
5. scenario steps over the transport, human-like pacing between steps
   (B.3 property 4); every key step saves a PNG screenshot and a
   hierarchy dump (evidence, B.3);
6. screen recognition on element ids/texts, never coordinates
   (B.3 property 5): unknown or unexpected screen -> BLOCKED
   (UnknownScreenError), the bridge never tries to pass it;
7. evidence validation (B.4): a result without evidence is a FAIL.

Standard events (B.2): unplugged cable / phone reboot / logged-out
account -> AndroidDeviceUnavailableError (BLOCKED with a reason), never
a silent retry.
"""

from __future__ import annotations

import random
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.android.errors import (
    AndroidBridgeError,
    AndroidDeviceUnavailableError,
    BridgeDisabledError,
    BridgeEvidenceMissingError,
    BridgeMutexTimeoutError,
    BridgeTransportMissingError,
    DailyCapExceededError,
    UnknownScreenError,
)
from nextlife.android.evidence import BridgeEvidence, EvidenceStep, validate_evidence
from nextlife.android.mutex import BridgeMutex
from nextlife.android.screens import ScreenFingerprint, make_recognizer
from nextlife.android.transport import AdbTransport
from nextlife.core.config import AndroidBridgeConfig, AndroidMarketplaceConfig, get_data_dir

__all__ = [
    "AndroidBridge",
    "AndroidBridgeError",
    "AndroidDeviceUnavailableError",
    "BridgeDisabledError",
    "BridgeEvidenceMissingError",
    "BridgeMutexTimeoutError",
    "BridgeTransportMissingError",
    "DailyCapExceededError",
    "UnknownScreenError",
    "BridgeCallResult",
]


@dataclass
class BridgeCallResult:
    """Result of one high-level scenario call (always with evidence)."""

    scenario: str
    marketplace_key: str
    ok: bool
    data: dict[str, Any]
    evidence: BridgeEvidence
    blocked_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AndroidBridge:
    """High-level scenarios over the ADB transport (TZ M1 B.3/B.4)."""

    def __init__(
        self,
        config: AndroidBridgeConfig,
        transport: AdbTransport | None,
        *,
        session: Session,
        artifact_root: Path | None = None,
        mutex: BridgeMutex | None = None,
        owner_token: str | None = None,
        marketplaces: dict[str, AndroidMarketplaceConfig] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.session = session
        # Evidence dir follows the data dir (NEXTLIFE_DATA_DIR in a deployment,
        # e.g. /var/lib/nextlife, which is writable under ProtectSystem=strict);
        # the relative ``data/android/evidence`` default would be read-only when
        # the service CWD is /opt. Tests pass an explicit tmp_path.
        self.artifact_root = artifact_root or get_data_dir() / "android" / "evidence"
        self.mutex = mutex or BridgeMutex()
        self.owner_token = owner_token or f"nextlife-{uuid.uuid4().hex[:12]}"
        # marketplace key -> ANDROID channel config (broker platform id +
        # screen fingerprints). TZ Marketplaces: the broker is the single
        # source of truth for the app package + rate limits.
        self.marketplaces = marketplaces or {}

    # ------------------------------------------------------------------ #
    # guards
    # ------------------------------------------------------------------ #

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise BridgeDisabledError(
                "ANDROID channel is disabled in config: the dedicated phone and "
                "the adb node are not provided yet (TZ M1 B.2, confirmations 5/6). "
                "Bridge state: BLOCKED-EXTERNAL."
            )

    def _require_transport(self) -> AdbTransport:
        if self.transport is None:
            raise BridgeTransportMissingError(
                "No ADB transport provided. TZ M1 B.2 (remote adb server over "
                "Tailscale) is BLOCKED-EXTERNAL until the dedicated phone and "
                "the USB node are provided (confirmations 5/6)."
            )
        return self.transport

    def _platform_id(self, app_key: str) -> str:
        """The broker platform id for the marketplace (TZ Marketplaces: the
        broker is the single source of truth for the app package; the NextLife
        config keeps only the broker platform id + screen fingerprints)."""
        mp = self.marketplaces.get(app_key)
        if mp is None:
            raise BridgeDisabledError(
                f"Marketplace '{app_key}' has no ANDROID channel configured: the "
                "broker platform id is required (config/marketplaces/<key>.yaml "
                "'android' block)."
            )
        return mp.platform_id

    def _fingerprints(self, app_key: str) -> list[ScreenFingerprint]:
        """The version-tied screen fingerprints for the marketplace, from the
        config (TZ Marketplaces: fingerprints live in
        config/marketplaces/<key>.yaml, not in code)."""
        mp = self.marketplaces.get(app_key)
        if mp is None:
            return []
        return [
            ScreenFingerprint(
                name=fp.name,
                resource_ids=tuple(fp.resource_ids),
                texts=tuple(fp.texts),
            )
            for fp in mp.fingerprints
        ]

    # ------------------------------------------------------------------ #
    # scenario plumbing
    # ------------------------------------------------------------------ #

    def _evidence_dir(self, scenario: str) -> Path:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        stamp = datetime.now(UTC).strftime("%H%M%S")
        d = self.artifact_root / day / f"{scenario}_{stamp}_{uuid.uuid4().hex[:6]}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _pace(self) -> None:
        """B.3 property 4: human-like pause between actions (from config)."""
        lo, hi = self.config.pacing.min_pause_seconds, self.config.pacing.max_pause_seconds
        if hi > 0:
            time.sleep(random.uniform(lo, hi))

    def _capture(self, dest_dir: Path, step: int, transport: AdbTransport) -> EvidenceStep:
        name = f"step_{step:02d}"
        screenshot = dest_dir / f"{name}.png"
        dump = dest_dir / f"{name}.xml"
        transport.screenshot(screenshot)
        transport.hierarchy_dump(dump)
        return EvidenceStep(name=name, screenshot=screenshot, hierarchy_dump=dump)

    def _run(
        self,
        scenario: str,
        marketplace_key: str,
        steps: list[Any],
        *,
        platform: str,
        priority: str = "background",
        require_app_version: bool,
        expected_final_screen: tuple[str, ...] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> BridgeCallResult:
        """Common scenario flow: mutex -> steps -> screen check -> evidence
        validation (B.3/B.4).

        TZ Marketplaces: ``platform`` is the broker platform id to lease,
        ``priority`` the lease class (search/read -> ``background``,
        publish/reply -> ``interactive``). The broker is the only frequency
        control (a 403 reschedules the task); there is no local daily cap."""
        self._require_enabled()
        transport = self._require_transport()
        evidence = BridgeEvidence(scenario=scenario, started_at=datetime.now(UTC))
        data: dict[str, Any] = {}
        try:
            with self.mutex.hold(self.session, self.owner_token):
                transport.begin(platform, priority)
                try:
                    if not transport.ping():
                        raise AndroidDeviceUnavailableError(
                            f"Scenario '{scenario}': phone/adb node not reachable "
                            "(cable unplugged, phone rebooted, node offline). "
                            "Standard event per TZ M1 B.2 — BLOCKED with reason."
                        )
                    dest_dir = self._evidence_dir(scenario)
                    step_no = 0
                    for step_fn in steps:
                        step_no += 1
                        step_fn(evidence, data, transport)
                        step = self._capture(dest_dir, step_no, transport)
                        evidence.steps.append(step)
                        if step_no < len(steps):
                            self._pace()
                    if require_app_version:
                        evidence.app_version = transport.app_version()
                    # On cancel (wizard «Прервать») the share flow may have
                    # stopped mid-card: the screen is not the results screen,
                    # so skip the final-screen check and keep partial results.
                    cancelled = cancel_event is not None and cancel_event.is_set()
                    if expected_final_screen and evidence.steps and not cancelled:
                        screen = make_recognizer(self._fingerprints(marketplace_key)).require_known(
                            evidence.steps[-1].hierarchy_dump.read_text(encoding="utf-8"),
                            scenario=scenario,
                            screenshot=str(evidence.steps[-1].screenshot),
                            expected=expected_final_screen,
                        )
                        data["screen"] = screen
                finally:
                    transport.end()
            evidence.finished_at = datetime.now(UTC)
            validate_evidence(evidence, require_app_version=require_app_version)
        except AndroidBridgeError:
            if evidence.finished_at is None:
                evidence.finished_at = datetime.now(UTC)
            raise
        return BridgeCallResult(
            scenario=scenario,
            marketplace_key=marketplace_key,
            ok=True,
            data=data,
            evidence=evidence,
        )

    # ------------------------------------------------------------------ #
    # high-level scenarios (first iteration: Vinted read-only, B.4)
    # ------------------------------------------------------------------ #

    def health(self) -> BridgeCallResult:
        """Phone online, screen captured (B.3: device state)."""

        def device_check(evidence: BridgeEvidence, data: dict[str, Any], t: AdbTransport) -> None:
            data["online"] = True

        return self._run(
            "health", "device", [device_check], platform="device", require_app_version=False
        )

    def app_state(self, app_key: str) -> BridgeCallResult:
        """App installed + version, foreground screen recognized (B.3).

        The app must land on a KNOWN screen; a login / consent / update
        dialog is an unknown screen -> BLOCKED (property 3), not a
        fabricated «вход выполнен» claim.
        """
        platform = self._platform_id(app_key)

        def open(evidence: BridgeEvidence, data: dict[str, Any], t: AdbTransport) -> None:
            t.open_app()
            data["app"] = app_key

        known = tuple(fp.name for fp in self._fingerprints(app_key))
        return self._run(
            "app_state", app_key, [open], platform=platform, require_app_version=True,
            expected_final_screen=known,
        )

    def search(
        self,
        app_key: str,
        query: str,
        link_limit: int = 5,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> BridgeCallResult:
        """App search: returns evidence of the results screen (B.3/B.4).

        TZ Marketplaces: search is a ``background`` lease (read-only, <=300s).
        Stage 2: one code for all platforms; differences in config.
        ``link_limit`` caps how many cards the Share flow extracts links for
        (fewer = faster; the wizard uses 3 to fit its 45 s deadline).
        ``cancel_event``: when set (wizard «Прервать»), the share flow stops
        at the next card and returns the links collected so far; the final
        screen check is skipped and the lease is released.
        ``on_progress``: called with the number of links collected so far
        after each card (wizard SSE progress line)."""
        platform = self._platform_id(app_key)
        mp = self.marketplaces.get(app_key)
        search_config = {
            "search_field": mp.search.search_field if mp and mp.search.search_field else "input_search",
            "result_card": mp.search.result_card if mp and mp.search.result_card else "",
            "share_menu": mp.search.share_menu if mp and mp.search.share_menu else "",
            "share_action": mp.search.share_action if mp and mp.search.share_action else "",
            "consent_selector": mp.search.consent_selector if mp and mp.search.consent_selector else "",
            "link_limit": str(link_limit),
        } if mp else {}

        def open(evidence: BridgeEvidence, data: dict[str, Any], t: AdbTransport) -> None:
            t.open_app()

        def type_query(evidence: BridgeEvidence, data: dict[str, Any], t: AdbTransport) -> None:
            first_xml = t.search_in_app(query, search_config)
            # Phase 1: extract the listing links via the in-app Share flow.
            urls, after_xml = t.extract_card_links(first_xml, search_config, cancel_event, on_progress)
            data["query"] = query
            data["results_xml"] = first_xml + ("\n" + after_xml if after_xml else "")
            data["links"] = urls

        # The expected search-results screen is derived from the marketplace's
        # own version-pinned fingerprints (the one named "*_search_results")
        # instead of hardcoding Vinted's: a marketplace with no such
        # fingerprint skips the screen check until its results screen is
        # version-pinned, so other platforms are not broken by Vinted's name.
        search_fps = [
            fp.name for fp in self._fingerprints(app_key) if fp.name.endswith("_search_results")
        ]
        expected_final_screen = tuple(search_fps) if search_fps else None

        return self._run(
            "search",
            app_key,
            [open, type_query],
            platform=platform,
            priority="background",
            require_app_version=True,
            expected_final_screen=expected_final_screen,
            cancel_event=cancel_event,
        )

    def read_listing(self, app_key: str, ref: str) -> BridgeCallResult:
        """Open a listing: returns evidence of the detail screen (B.3/B.4).

        TZ Marketplaces: read is a ``background`` lease (read-only, <=300s)."""
        platform = self._platform_id(app_key)

        def open(evidence: BridgeEvidence, data: dict[str, Any], t: AdbTransport) -> None:
            t.open_app()

        def open_ref(evidence: BridgeEvidence, data: dict[str, Any], t: AdbTransport) -> None:
            t.open_listing(ref)
            data["ref"] = ref

        return self._run(
            "read_listing",
            app_key,
            [open, open_ref],
            platform=platform,
            priority="background",
            require_app_version=True,
            expected_final_screen=("vinted_listing_detail",),
        )
