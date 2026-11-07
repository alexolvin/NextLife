"""Transport contract of the ANDROID bridge (TZ M1 B.3) + the phonebroker
implementation.

The module ``android`` (this package) is the SINGLE point of contact with
the phone: business logic calls high-level scenarios only, never touches
or coordinates. The transport is the low-level layer that talks to the
phone.

Two implementations:
- ``AdbTransport`` (Protocol) — the contract. Tests use fakes of it.
- ``PhoneBrokerTransport`` — talks to the phonebroker HTTP API (the
  single-phone ADB broker, 127.0.0.1:8090). Raw ADB is blocked by
  nftables for every user except the broker, so this is the only way in.

Lease lifecycle (B.3): the broker mediates EXCLUSIVE access via a
lease. ``begin()`` acquires the lease (exclusive phone), keeps it alive
with heartbeats, and wakes the screen; ``end()`` releases it. The bridge
holds the global mutex around a scenario, so one lease per scenario.
"""

from __future__ import annotations

import base64
import html
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from nextlife.android.errors import (
    AndroidBridgeError,
    AndroidDeviceUnavailableError,
    BridgeTransportMissingError,
    BrokerAppNotInstalledError,
    BrokerLimitExceededError,
    BrokerMaintenanceError,
)
from nextlife.core.exceptions import SecretResolutionError
from nextlife.core.secrets import resolve_secret

if TYPE_CHECKING:
    from nextlife.core.config import PhoneBrokerConfig


class AdbTransport(Protocol):
    """Low-level phone operations. One active lease = exclusive phone
    access; the queue-level mutex (B.3) serializes scenarios, so this
    protocol must NOT be called concurrently.

    TZ Marketplaces: the broker is the single source of truth for the app
    package — the transport methods do NOT take a package. ``platform`` (the
    broker platform id) and ``priority`` (the lease class) are per-operation:
    search/read -> ``background``, publish/reply -> ``interactive``."""

    def begin(self, platform: str, priority: str = "background") -> None:
        """Acquire exclusive access for one scenario (raise if the phone
        is unavailable). ``platform`` is the broker platform id to lease;
        ``priority`` is the lease class for this operation: search/read ->
        ``background``, publish/reply -> ``interactive`` (per-operation, not a
        single config value)."""
        ...

    def end(self) -> None:
        """Release exclusive access at the end of the scenario."""
        ...

    def ping(self) -> bool:
        """Phone is online (broker reports ``device``)."""
        ...

    def screenshot(self, dest: Path) -> Path:
        """Capture the current screen as PNG to ``dest``; returns the path."""
        ...

    def hierarchy_dump(self, dest: Path) -> Path:
        """Capture the current uiautomator hierarchy dump (XML) to ``dest``."""
        ...

    def app_version(self) -> str:
        """Installed versionName of the leased platform app (from the broker
        ``/health``); empty string when the broker does not report it yet."""
        ...

    def open_app(self) -> None:
        """Bring the leased platform app to the foreground."""
        ...

    def search_in_app(self, query: str, search_config: dict[str, str] | None = None) -> str:
        """Type a query into the app's search and submit it.

        Returns the FIRST results screen XML (no scroll). ``search_config``
        carries per-platform selectors (search_field, result_card) — Stage 2:
        one code, config diffs."""
        ...

    def extract_card_links(
        self,
        first_xml: str,
        search_config: dict[str, str] | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> tuple[list[str], str]:
        """Extract the listing links for the result cards (Phase 1, Share flow).

        Returns ``(urls, after_scroll_xml)`` — see ``PhoneBrokerTransport``.
        ``cancel_event``: when set, the loop stops early and returns the
        links collected so far (the wizard's «Прервать» releases the phone).
        ``on_progress``: called with the number of links collected so far
        after each card (wizard SSE progress line)."""
        ...

    def open_listing(self, ref: str) -> None:
        """Open a listing by its in-app reference (deeplink)."""
        ...


class PhoneBrokerTransport:
    """AdbTransport over the phonebroker HTTP API (127.0.0.1:8090).

    The broker mediates exclusive access to the single phone via a
    lease/queue. This transport:
    - ``begin()``: POST /lease (platform) + heartbeat thread + wake screen;
    - ops: launch / screenshot / ui_dump / keyevent / swipe / tap;
    - ``end()``: DELETE /lease.

    The screen is re-woken before every capture (screenshot / dump) because
    it otherwise drifts into AOD during the human-like pacing pauses.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8090",
        token: str,
        lease_max_duration_s: int = 300,
        heartbeat_interval_s: float = 15.0,
        wake_swipe: tuple[int, int, int, int] = (540, 2000, 540, 500),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.lease_max_duration_s = lease_max_duration_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self._wake_swipe = wake_swipe
        self._lease_id: str | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        # versionName of the leased platform app, cached from /health.
        self._app_version: str = ""

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw.decode(errors="replace")}

    def _op(self, op: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self._lease_id is not None, "no active lease"
        st, d = self._req("POST", f"/lease/{self._lease_id}/ops/{op}", body)
        if st != 200:
            raise self._status_error(op, st, d)
        return d

    @staticmethod
    def _status_error(op: str, st: int, d: dict[str, Any]) -> AndroidBridgeError:
        """Map a broker non-200 to a named task status (TZ Marketplaces):
        423 maintenance (retry later), 403 rate limit (reschedule), 409 app
        not installed (block), 503 phone down. The broker answers with a task
        status + reason, NOT a connector error — so each maps to its own
        exception, not a blanket "device unavailable"."""
        detail = d.get("detail", d) if isinstance(d, dict) else d
        if st == 423:
            return BrokerMaintenanceError(
                f"phonebroker '{op}': 423 maintenance — owner servicing the phone, "
                f"retry later ({detail})"
            )
        if st == 403:
            return BrokerLimitExceededError(
                f"phonebroker '{op}': 403 rate limit (interval/daily) — reschedule ({detail})"
            )
        if st == 409:
            return BrokerAppNotInstalledError(
                f"phonebroker '{op}': 409 app not installed ({detail})"
            )
        if st == 503:
            return AndroidDeviceUnavailableError(
                f"phonebroker '{op}': 503 phone unavailable ({detail})"
            )
        return AndroidDeviceUnavailableError(
            f"phonebroker op '{op}' failed ({st}): {d}"
        )

    # ------------------------------------------------------------------ #
    # lease lifecycle
    # ------------------------------------------------------------------ #

    def begin(self, platform: str, priority: str = "background") -> None:
        """Acquire the lease, start heartbeats, wake the screen.

        ``platform`` is the broker platform id to lease; ``priority`` is the
        lease class for this operation: search/read -> ``background``,
        publish/reply -> ``interactive`` (TZ Marketplaces: per-operation)."""
        if self._lease_id is not None:
            return  # already active (idempotent)
        st, d = self._req(
            "POST",
            "/lease",
            {
                "platform": platform,
                "operation": "nextlife-bridge",
                "priority": priority,
                "max_duration_s": self.lease_max_duration_s,
            },
        )
        if st not in (201, 202):
            raise AndroidDeviceUnavailableError(
                f"phonebroker lease rejected ({st}): {d}"
            )
        self._lease_id = d["id"]
        if d.get("status") == "waiting":
            # Poll until active or revoked.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                time.sleep(3)
                _, cur = self._req("GET", f"/lease/{self._lease_id}")
                if cur.get("status") == "active":
                    break
                if cur.get("status") in ("revoked", "completed"):
                    self._lease_id = None
                    raise AndroidDeviceUnavailableError(
                        f"phonebroker lease did not activate: {cur}"
                    )
            else:
                self._lease_id = None
                raise AndroidDeviceUnavailableError(
                    "phonebroker lease timed out waiting for the phone"
                )
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()
        self._cache_version(platform)
        self._wake()

    def _cache_version(self, platform: str) -> None:
        """Cache the versionName of the leased platform app from ``/health``
        (TZ Marketplaces: the broker is the source of the app version).
        Graceful when the broker does not report it yet (empty string)."""
        st, d = self._req("GET", "/health")
        if st != 200:
            self._app_version = ""
            return
        for p in d.get("platforms", []):
            if isinstance(p, dict) and p.get("platform") == platform:
                # The live broker reports ``version_name`` (snake_case); the
                # test fake broker uses ``versionName`` — accept both.
                self._app_version = str(p.get("version_name") or p.get("versionName") or "")
                return
        self._app_version = ""

    def end(self) -> None:
        """Release the lease (idempotent)."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None
        if self._lease_id is not None:
            self._req("DELETE", f"/lease/{self._lease_id}")
            self._lease_id = None

    def _heartbeat_loop(self) -> None:
        assert self._lease_id is not None
        while not self._heartbeat_stop.wait(self.heartbeat_interval_s):
            self._req("POST", f"/lease/{self._lease_id}/heartbeat")

    def _wake(self) -> None:
        """Wake the screen (idempotent). The broker handles keyguard
        dismissal at lease start; this only re-wakes for AOD drift."""
        if self._lease_id is None:
            return
        self._op("keyevent", {"keycode": 224})  # WAKEUP
        time.sleep(0.5)

    # ------------------------------------------------------------------ #
    # AdbTransport
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        st, d = self._req("GET", "/health")
        return st == 200 and d.get("phone") == "device"

    def screenshot(self, dest: Path) -> Path:
        self._wake()
        d = self._op("screenshot")
        dest.write_bytes(base64.b64decode(d["image"]))
        return dest

    def hierarchy_dump(self, dest: Path) -> Path:
        self._wake()
        d = self._op("ui_dump")
        dest.write_text(d["xml"], encoding="utf-8")
        return dest

    # Low-level broker ops (used by the high-level scenarios, e.g. search).
    # The selector format is 'resource-id=value' / 'text=value' /
    # 'content-desc=value' (the broker matches against the current ui_dump).

    def _text(self, text: str) -> None:
        """Type Unicode text into the focused field (broker `text` op)."""
        self._op("text", {"text": text})

    def push_media(self, paths: list[Path]) -> dict[str, Any]:
        """Upload images into this lease (broker ``push_media``, multipart field ``files``).

        The broker stores them under ``/sdcard/Pictures/phonebroker/<lease>/`` and
        removes them when the lease ends. jpg, jpeg, png, webp only.
        """
        if self._lease_id is None:
            raise AndroidDeviceUnavailableError("phonebroker push_media: no active lease")
        if not paths:
            raise AndroidDeviceUnavailableError("phonebroker push_media: no files")
        boundary = f"----nl{uuid.uuid4().hex}"
        body = bytearray()
        for path in paths:
            payload = path.read_bytes()
            filename = path.name.replace('"', "")
            body += f"--{boundary}\r\n".encode()
            body += (
                f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            ).encode()
            body += b"Content-Type: application/octet-stream\r\n\r\n"
            body += payload
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            f"{self.base_url}/lease/{self._lease_id}/ops/push_media",
            data=bytes(body),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                detail = json.loads(raw)
            except Exception:
                detail = {"raw": raw.decode(errors="replace")}
            raise self._status_error("push_media", e.code, detail) from e

    def _tap_selector(self, selector: str) -> None:
        """Tap a UI element by selector (broker `tap_selector` op)."""
        self._op("tap_selector", {"selector": selector})

    def _wait_for(self, selector: str, timeout_s: int = 30) -> None:
        """Wait for a UI element by selector (broker `wait_for` op)."""
        self._op("wait_for", {"selector": selector, "timeout_s": timeout_s})

    def scroll_down(self) -> str:
        """Swipe up to scroll down and return the new screen XML."""
        self._op("swipe", {"x1": 540, "y1": 1500, "x2": 540, "y2": 500, "duration_ms": 500})
        time.sleep(2)
        self._wake()
        d = self._op("ui_dump")
        return d.get("xml", "")

    def app_version(self) -> str:
        """versionName of the leased platform app from the broker ``/health``
        (cached on ``begin``). Empty when the broker does not report it yet —
        the bridge then skips the version check and relies on the fingerprint."""
        return self._app_version

    def open_app(self) -> None:
        # The broker launches the lease's platform package (set in begin()).
        try:
            self._op("launch")
        except AndroidDeviceUnavailableError as exc:
            if "permissioncontroller" in str(exc):
                # An Android runtime permission dialog (notifications / camera) is on top
                time.sleep(1)
                for pt in ((540, 1765), (540, 1936)):
                    try:
                        self._op("tap", {"x": pt[0], "y": pt[1]})
                        time.sleep(1)
                    except Exception:
                        pass
                self._op("launch")
            else:
                raise

    def _dismiss_consent(self, consent_selector: str) -> None:
        """Tap a launch-time consent/onboarding button if it is present.

        ``consent_selector`` is a ui_dump selector (e.g. ``text=Přijmout vše``).
        After first acceptance the screen does not recur, so the common case
        is a no-op (one extra ui_dump). Best-effort: dump/tap failures are
        swallowed — the search flow then reports the real error (e.g. the
        search field still hidden)."""
        _, consent_value = consent_selector.split("=", 1)
        try:
            self._wake()
            d = self._op("ui_dump")
            xml = d.get("xml", "")
        except Exception:
            return
        # The consent text may appear literal or HTML-entity-encoded in the dump.
        if consent_value not in xml and consent_value not in html.unescape(xml):
            return
        try:
            self._tap_selector(consent_selector)
            time.sleep(2)
        except Exception:
            pass

    def search_in_app(self, query: str, search_config: dict[str, str] | None = None) -> str:
        """Search in the app: tap search field → type query → ENTER → wait for results.

        Stage 2: one code for all platforms; differences in ``search_config``.
        ``search_field`` is a ui_dump selector: ``resource-id=X``, ``content-desc=X``,
        or ``text=X``. Coordinates are not supported (owner rule 2026-10-09).
        Scrolls down until ``result_card`` nodes reach the desired count (max 3 swipes).
        Returns the last results screen XML for parsing."""
        cfg = search_config or {}
        search_field = cfg.get("search_field", "input_search")
        result_card = cfg.get("result_card", "")

        # Dismiss a launch-time consent/onboarding screen if configured and
        # present (e.g. Vinted's Czech cookie consent blocks input_search).
        consent_selector = cfg.get("consent_selector", "")
        if consent_selector:
            self._dismiss_consent(consent_selector)

        # Tap the search field via ui_dump selector
        if "=" in search_field:
            self._tap_selector(search_field)
        else:
            self._tap_selector(f"resource-id={search_field}")
        time.sleep(1)

        # Type the query
        self._text(query)
        time.sleep(0.5)

        # Press ENTER
        self._op("keyevent", {"keycode": 66})
        time.sleep(4)

        # Get the first results screen XML (no scroll)
        self._wake()
        d = self._op("ui_dump")
        return d.get("xml", "")

    def extract_card_links(
        self,
        first_xml: str,
        search_config: dict[str, str] | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> tuple[list[str], str]:
        """Extract the listing links for the result cards (Phase 1, Share flow).

        Processes the visible cards (tapped by their own content-desc), then
        scrolls down once for the 5th card. Returns ``(urls, after_scroll_xml)``
        where ``after_scroll_xml`` is the post-scroll screen (for the parser to
        reach 5 results). The link comes from the share sheet ui_dump (the URL
        is shown as text) -- no clipboard/paste needed.

        ``search_config`` must carry ``result_card``, ``share_menu`` and
        ``share_action``; without them, returns ``([], "")`` (no links).
        ``on_progress``: called with the number of links collected so far
        after each card (wizard SSE progress line)."""
        cfg = search_config or {}
        result_card = cfg.get("result_card", "")
        share_menu = cfg.get("share_menu", "")
        share_action = cfg.get("share_action", "")
        link_limit = int(cfg.get("link_limit", "5"))
        if not (result_card and share_menu):
            return [], ""

        urls: list[str] = []
        all_xml: list[str] = [first_xml]
        seen: set[str] = set()
        current_xml = first_xml
        # Process the visible cards, then scroll down for more until
        # ``link_limit`` links are collected (or no new cards remain). The
        # screen resets to the top after each card, so re-scrolling shows the
        # next set of cards. The limit keeps the share flow fast enough for
        # the wizard's 45 s deadline. ``cancel_event`` (wizard «Прервать»):
        # stop at the next card boundary and return the links collected so
        # far, so the lease is released promptly.
        for _ in range(4):
            if cancel_event is not None and cancel_event.is_set():
                break
            selectors = self._card_tap_selectors(current_xml, result_card)
            new_selectors = [s for s in selectors if s not in seen]
            for selector in new_selectors:
                if len(urls) >= link_limit:
                    break
                if cancel_event is not None and cancel_event.is_set():
                    break
                seen.add(selector)
                urls.append(self._extract_one_link(selector, share_menu, share_action))
                if on_progress is not None:
                    on_progress(len(urls))
            if len(urls) >= link_limit:
                break
            current_xml = self.scroll_down()
            all_xml.append(current_xml)
        return urls, "\n".join(all_xml)

    def _extract_one_link(self, card_selector: str, share_menu: str, share_action: str) -> str:
        """Extract one card's link: tap card, share menu, share action, read
        the URL from the share sheet, BACK BACK to the results."""
        try:
            self._tap_selector(card_selector)
            time.sleep(3)
            self._tap_selector(share_menu)
            time.sleep(2)
            # share_action is optional: some apps (Kleinanzeigen) open the
            # share sheet directly from share_menu; others (Vinted) show a
            # menu first.
            if share_action:
                self._tap_selector(share_action)
                time.sleep(3)
            self._wake()
            d = self._op("ui_dump")
            url = self._extract_url(d.get("xml", ""))
            # BACK (dismiss share sheet) + BACK (back to results).
            self._op("keyevent", {"keycode": 4})
            time.sleep(1)
            self._op("keyevent", {"keycode": 4})
            time.sleep(2)
            return url
        except Exception:
            # Recover: return to the results screen.
            for _ in range(2):
                try:
                    self._op("keyevent", {"keycode": 4})
                    time.sleep(1)
                except Exception:
                    break
            return ""

    @staticmethod
    def _card_tap_selectors(xml: str, result_card: str) -> list[str]:
        """The tap-selectors (``content-desc=<desc>``) for the result cards.

        A card is identified by ``result_card`` (a resource-id, or a
        ``content-desc:PATTERN``); its unique tap-selector is its own
        content-desc. Deduplicated, in screen order."""
        selectors: list[str] = []
        seen: set[str] = set()
        use_desc = result_card.startswith("content-desc:")
        desc_pattern = result_card[len("content-desc:"):] if use_desc else ""
        for m in re.finditer(r"<node[^>]*>", xml):
            s = m.group(0)
            desc_m = re.search(r'content-desc="([^"]*)"', s)
            raw_desc = desc_m.group(1) if desc_m else ""
            # The broker matches against ElementTree-decoded attributes, so
            # decode HTML entities (&#10; -> newline, &amp; -> &).
            desc = html.unescape(raw_desc)
            if not desc or desc in seen:
                continue
            if use_desc:
                if desc_pattern in desc:
                    seen.add(desc)
                    selectors.append(f"content-desc={desc}")
            else:
                rid_m = re.search(r'resource-id="([^"]*)"', s)
                if rid_m and rid_m.group(1) == result_card:
                    seen.add(desc)
                    selectors.append(f"content-desc={desc}")
        return selectors

    @staticmethod
    def _extract_url(xml: str) -> str:
        """The first http(s) URL in the share sheet XML.

        The URL may be a standalone text node (Vinted) or embedded in a
        longer share text (Kleinanzeigen: "Gerade bei #Kleinanzeigen
        gefunden... https://...")."""
        for m in re.finditer(r'text="([^"]*https?://[^"]*)"', xml):
            text = m.group(1).replace("&amp;", "&")
            url_m = re.search(r"https?://\S+", text)
            if url_m:
                return url_m.group(0)
        return ""

    def open_listing(self, ref: str) -> None:
        raise AndroidDeviceUnavailableError(
            "open_listing over phonebroker is not implemented yet "
            "(the broker launch op only starts the platform's main activity; "
            "deeplinks are not exposed)."
        )


def make_phone_broker_transport(config: PhoneBrokerConfig) -> PhoneBrokerTransport:
    """Build a PhoneBrokerTransport from config (token via resolve_secret)."""
    try:
        token = resolve_secret(config.token_secret_ref)
    except SecretResolutionError as e:
        raise BridgeTransportMissingError(
            f"phonebroker token secret {config.token_secret_ref} is not resolvable: {e}"
        ) from e
    if not token:
        raise BridgeTransportMissingError(
            f"phonebroker token secret {config.token_secret_ref} is empty."
        )
    return PhoneBrokerTransport(
        base_url=config.base_url,
        token=token,
        lease_max_duration_s=config.lease_max_duration_s,
    )
