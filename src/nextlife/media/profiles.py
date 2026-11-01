"""Marketplace media specifications and profile loader (TZ v2.0 Section IV.1)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"
DEFAULTS_FILE = CONFIG_DIR / "media_defaults.yaml"


class MediaProfile(BaseModel):
    marketplace_key: str
    max_photos: int = 10
    min_width: int = 800
    min_height: int = 600
    max_width: int = 1920
    max_height: int = 1440
    aspect_preferred: str = "4:3"
    max_file_bytes: int = 5242880  # 5MB
    formats: list[str] = Field(default_factory=lambda: ["JPEG", "PNG"])
    source_url: str | None = None
    verified_at: str | None = None

    # DoD: If verified_at is null, variant is flagged as unverified_profile
    unverified_profile: bool = False


def load_conservative_defaults() -> dict[str, Any]:
    """Load conservative fallback settings from config/media_defaults.yaml."""
    if DEFAULTS_FILE.exists():
        try:
            data = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8")) or {}
            return dict(data.get("media_defaults", {}))
        except Exception:
            pass
    return {
        "max_photos": 10,
        "min_width": 800,
        "min_height": 600,
        "max_width": 1920,
        "max_height": 1440,
        "aspect_preferred": "4:3",
        "max_file_bytes": 5242880,
        "formats": ["JPEG", "PNG"],
    }


def load_marketplace_media_profile(
    marketplace_key: str,
    custom_config_dir: Path | str | None = None,
) -> tuple[MediaProfile, str]:
    """Load media profile for a marketplace.

    DoD:
    - If verified_at is null, applies conservative defaults and marks unverified_profile = True.
    - Computes deterministic profile_hash for idempotent variant generation.
    """
    cfg_dir = Path(custom_config_dir) if custom_config_dir else (CONFIG_DIR / "marketplaces")
    mp_file = cfg_dir / f"{marketplace_key}.yaml"

    mp_data: dict[str, Any] = {}
    if mp_file.exists():
        try:
            mp_data = yaml.safe_load(mp_file.read_text(encoding="utf-8")) or {}
        except Exception:
            mp_data = {}

    media_sec = mp_data.get("media", {})
    verified_at = media_sec.get("verified_at")
    source_url = media_sec.get("source_url")

    # If verified_at is null, fallback to conservative profile
    is_unverified = verified_at is None

    if is_unverified:
        defaults = load_conservative_defaults()
        profile = MediaProfile(
            marketplace_key=marketplace_key,
            max_photos=int(defaults.get("max_photos", 10)),
            min_width=int(defaults.get("min_width", 800)),
            min_height=int(defaults.get("min_height", 600)),
            max_width=int(defaults.get("max_width", 1920)),
            max_height=int(defaults.get("max_height", 1440)),
            aspect_preferred=str(defaults.get("aspect_preferred", "4:3")),
            max_file_bytes=int(defaults.get("max_file_bytes", 5242880)),
            formats=list(defaults.get("formats", ["JPEG", "PNG"])),
            source_url=source_url,
            verified_at=None,
            unverified_profile=True,
        )
    else:
        profile = MediaProfile(
            marketplace_key=marketplace_key,
            max_photos=int(media_sec.get("max_photos", 10)),
            min_width=int(media_sec.get("min_width", 800)),
            min_height=int(media_sec.get("min_height", 600)),
            max_width=int(media_sec.get("max_width", 1920)),
            max_height=int(media_sec.get("max_height", 1440)),
            aspect_preferred=str(media_sec.get("aspect_preferred", "4:3")),
            max_file_bytes=int(media_sec.get("max_file_bytes", 5242880)),
            formats=list(media_sec.get("formats", ["JPEG"])),
            source_url=source_url,
            verified_at=str(verified_at),
            unverified_profile=False,
        )

    # Compute deterministic profile hash
    canonical = json.dumps(profile.model_dump(), sort_keys=True, ensure_ascii=False)
    profile_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return profile, profile_hash
