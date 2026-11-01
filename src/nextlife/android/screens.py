"""Screen recognizer of the ANDROID bridge (TZ M1 B.3 property 5, B.5 step B5).

Recognition is based on ELEMENT IDENTIFIERS AND TEXTS, never on
coordinates: fingerprints match ``resource-id`` and ``text`` values from
the uiautomator hierarchy dump. A known screen is recognized by name; an
unknown screen (captcha, identity check, update prompt, consent dialog,
any unknown window) gives BLOCKED — the bridge never tries to pass it
(B.3 property 3). Mutation D.3-7: letting an unknown screen through must
turn the unknown-screen test RED.

NOTE (Ч-6): the Vinted fingerprints below are REAL references, captured
from the live device on 2026-10-07 (B6) via the phonebroker ui_dump.
They must be re-verified after any Vinted app update (the Bloom/Compose
ids can change). The recognizer mechanism itself is fixture-tested here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from nextlife.android.errors import UnknownScreenError


@dataclass(frozen=True)
class ScreenFingerprint:
    """A known screen: element ids and texts that identify it.

    No coordinates (B.3 property 5): matching uses resource-id / text
    only, so screen density, rotation and layout shifts do not break it.
    """

    name: str
    resource_ids: tuple[str, ...]
    texts: tuple[str, ...] = ()

    def matches(self, ids: set[str], texts: set[str]) -> bool:
        if not self.resource_ids and not self.texts:
            return False
        if self.resource_ids and not all(rid in ids for rid in self.resource_ids):
            return False
        if self.texts and not any(t in texts for t in self.texts):
            return False
        return True



def parse_hierarchy_ids_and_texts(hierarchy_xml: str) -> tuple[set[str], set[str]]:
    """Extract resource-id and text sets from a uiautomator hierarchy dump."""
    root = ET.fromstring(hierarchy_xml)
    ids: set[str] = set()
    texts: set[str] = set()
    for node in root.iter("node"):
        rid = node.get("resource-id")
        if rid:
            ids.add(rid)
        text = node.get("text")
        if text:
            texts.add(text)
    return ids, texts


class ScreenRecognizer:
    """Recognizes known screens from hierarchy dumps; unknown -> BLOCKED."""

    def __init__(self, fingerprints: tuple[ScreenFingerprint, ...] | list[ScreenFingerprint]) -> None:
        self.fingerprints = tuple(fingerprints)

    def recognize(self, hierarchy_xml: str) -> str | None:
        """Return the known screen name, or None when the screen is unknown."""
        ids, texts = parse_hierarchy_ids_and_texts(hierarchy_xml)
        for fp in self.fingerprints:
            if fp.matches(ids, texts):
                return fp.name
        return None

    def require_known(
        self,
        hierarchy_xml: str,
        *,
        scenario: str,
        screenshot: str | None = None,
        expected: tuple[str, ...] | None = None,
    ) -> str:
        """Return the recognized name, raising UnknownScreenError (BLOCKED)
        on an unknown screen or on a screen that is known but not the one
        the scenario expects (never continue with a wrong screen)."""
        name = self.recognize(hierarchy_xml)
        if name is None:
            raise UnknownScreenError(
                f"Scenario '{scenario}': unknown screen (no known fingerprint matched). "
                f"BLOCKED — the bridge does not try to pass it."
                + (f" Screenshot: {screenshot}" if screenshot else "")
            )
        if expected and name not in expected:
            raise UnknownScreenError(
                f"Scenario '{scenario}': unexpected screen '{name}' "
                f"(expected one of {', '.join(expected)}). BLOCKED."
            )
        return name


def make_recognizer(fingerprints: list[ScreenFingerprint]) -> ScreenRecognizer:
    """Recognizer built from the marketplace's config fingerprints
    (TZ Marketplaces: the fingerprints are version-tied and live in
    config/marketplaces/<key>.yaml, not in code)."""
    return ScreenRecognizer(fingerprints)
