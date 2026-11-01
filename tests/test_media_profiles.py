from nextlife.media.profiles import load_marketplace_media_profile


def test_unverified_profile_triggers_fallback_and_flag(tmp_path):
    """DoD: Profile with verified_at: null triggers conservative defaults and unverified_profile=True."""
    profile, p_hash = load_marketplace_media_profile("bazos_cz")
    assert profile.unverified_profile is True
    assert profile.verified_at is None
    # Conservative limits are applied
    assert profile.min_width == 800
    assert profile.max_width == 1920
    assert profile.aspect_preferred == "4:3"
    assert len(p_hash) == 64


def test_verified_profile_has_flag_false(tmp_path):
    """Verify that a documented profile with verified_at has unverified_profile=False."""
    custom_cfg = tmp_path / "aukro_verified.yaml"
    custom_cfg.write_text("""
media:
  max_photos: 8
  min_width: 1024
  min_height: 768
  max_width: 2560
  max_height: 1920
  aspect_preferred: "16:9"
  max_file_bytes: 10485760
  formats: ["JPEG"]
  source_url: "https://aukro.cz/napoveda/fotografie"
  verified_at: "2026-09-01"
""", encoding="utf-8")

    profile, p_hash = load_marketplace_media_profile(
        marketplace_key="aukro_verified",
        custom_config_dir=tmp_path,
    )
    assert profile.unverified_profile is False
    assert profile.verified_at == "2026-09-01"
    assert profile.aspect_preferred == "16:9"
    assert profile.max_photos == 8
