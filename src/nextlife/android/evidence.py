"""Evidence layer of the ANDROID bridge (TZ M1 B.3/B.5 step B4, mutation D.3-6).

Every bridge call returns, besides the result, evidence: a PNG screenshot
on each key step, an interface hierarchy dump, the app version, and the
start/end timestamps. A result without evidence is a failure (Ч-5).

Recognition-related: evidence paths are filesystem artifacts; the caller
(orchestrator/UI) can attach them to owner notifications and reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nextlife.android.errors import BridgeEvidenceMissingError

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class EvidenceStep:
    """One key step of a scenario: screenshot + hierarchy dump of the screen."""

    name: str
    screenshot: Path
    hierarchy_dump: Path


@dataclass
class BridgeEvidence:
    """Mandatory evidence of one bridge call (TZ M1 B.3)."""

    scenario: str
    started_at: datetime
    finished_at: datetime | None = None
    app_version: str | None = None
    steps: list[EvidenceStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "app_version": self.app_version,
            "steps": [
                {
                    "name": s.name,
                    "screenshot": str(s.screenshot),
                    "hierarchy_dump": str(s.hierarchy_dump),
                }
                for s in self.steps
            ],
        }


def validate_evidence(evidence: BridgeEvidence, *, require_app_version: bool = True) -> None:
    """B4: a call without evidence is a FAIL.

    Checks (mutation D.3-6 must turn RED when any of them is removed):
    - at least one key step;
    - every step has an existing non-empty PNG screenshot and an existing
      non-empty XML hierarchy dump;
    - finished_at present and not before started_at;
    - app_version present when the scenario drives an app.
    """
    problems: list[str] = []

    if not evidence.steps:
        problems.append("no key steps recorded")
    for step in evidence.steps:
        shot = step.screenshot
        if not (shot.exists() and shot.stat().st_size > 0):
            problems.append(f"step '{step.name}': screenshot missing or empty ({shot})")
        else:
            with shot.open("rb") as f:
                if f.read(8) != _PNG_MAGIC:
                    problems.append(f"step '{step.name}': screenshot is not a PNG ({shot})")
        dump = step.hierarchy_dump
        if not (dump.exists() and dump.stat().st_size > 0):
            problems.append(f"step '{step.name}': hierarchy dump missing or empty ({dump})")
        else:
            head = dump.read_text(encoding="utf-8", errors="replace")[:64].lstrip().lower()
            if not head.startswith(("<hierarchy", "<?xml")):
                problems.append(f"step '{step.name}': hierarchy dump is not XML ({dump})")

    if evidence.finished_at is None:
        problems.append("finished_at is missing")
    elif evidence.finished_at < evidence.started_at:
        problems.append("finished_at is before started_at")

    if require_app_version and not evidence.app_version:
        problems.append("app_version is missing")

    if problems:
        raise BridgeEvidenceMissingError(
            f"Bridge evidence for scenario '{evidence.scenario}' is incomplete: "
            + "; ".join(problems)
        )
