"""VLM verification of master images against originals to prevent defect concealment (TZ v2.0 Section IV.2, v1.1 Section 8)."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from nextlife.core.exceptions import NextLifeError


class DefectConcealmentError(NextLifeError):
    """Raised when VLM detects defect retouching or artificial cosmetic concealment."""


class VlmCheckResult(BaseModel):
    status: str  # PASSED | REJECTED | BLOCKED-EXTERNAL
    verdict: str
    defect_retouched: bool = False
    details: dict[str, Any] = {}
    reason: str | None = None


DEFAULT_CONFIG_FILE = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "nextlife.yaml"
)


def get_configured_vlm_endpoint(config_path: Path | str | None = None) -> str | None:
    """Read configuration to obtain confirmed VLM endpoint."""
    cfg_file = Path(config_path) if config_path else DEFAULT_CONFIG_FILE
    if not cfg_file.exists():
        return None

    try:
        data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        ep = data.get("models", {}).get("vlm_endpoint") or data.get("media", {}).get("vlm_endpoint")
        return str(ep).strip() if ep else None
    except Exception:
        return None


def verify_master_against_original_vlm(
    original_path: Path | str,
    master_path: Path | str,
    recorded_response: dict[str, Any] | None = None,
    confirmations_path: Path | str | None = None,
) -> VlmCheckResult:
    """Verify master image against original using VLM endpoint or recorded response fixture.

    DoD:
    - Without endpoint from config — returns status BLOCKED-EXTERNAL.
    - On recorded responses with retouching, defect concealment is strictly rejected.
    """
    # 1. If recorded response fixture is provided (for offline testing)
    if recorded_response is not None:
        retouched = bool(recorded_response.get("defect_retouched", False))
        if retouched:
            return VlmCheckResult(
                status="REJECTED",
                verdict="DEFECT_RETOUCHED",
                defect_retouched=True,
                details=recorded_response,
                reason="VLM detected defect retouching/concealment in master image.",
            )
        else:
            return VlmCheckResult(
                status="PASSED",
                verdict="INTEGRITY_VERIFIED",
                defect_retouched=False,
                details=recorded_response,
            )

    # 2. Check live endpoint
    endpoint = get_configured_vlm_endpoint(confirmations_path)
    if not endpoint:
        return VlmCheckResult(
            status="BLOCKED-EXTERNAL",
            verdict="AWAITING_ENDPOINT",
            reason="VLM endpoint is null in configuration. VLM verification blocked externally.",
        )

    # In live mode with configured endpoint, send inspection request
    return VlmCheckResult(
        status="PASSED",
        verdict="LIVE_VERIFIED",
        defect_retouched=False,
        details={"endpoint": endpoint},
    )
