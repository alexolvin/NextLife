import pytest
from PIL import Image

from nextlife.media.profiles import MediaProfile
from nextlife.media.variants import (
    VariantGenerationError,
    generate_media_variant,
)


@pytest.fixture
def master_image_file(tmp_path):
    img_path = tmp_path / "master_sample.png"
    img = Image.new("RGB", (2400, 1600), color=(150, 180, 210))
    # Add patterns
    for x in range(0, 2400, 50):
        for y in range(0, 1600, 50):
            img.putpixel((x, y), (255, 0, 0))
    img.save(img_path, format="PNG")
    return img_path


def test_variant_conforms_to_profile_limits(master_image_file, tmp_path):
    """DoD: Variant fits strictly within profile dimensions, aspect ratio, and max_file_bytes."""
    profile = MediaProfile(
        marketplace_key="bazos_cz",
        max_photos=10,
        min_width=800,
        min_height=600,
        max_width=1280,
        max_height=960,
        aspect_preferred="4:3",
        max_file_bytes=500_000,  # 500 KB
        formats=["JPEG"],
    )

    out_variant = tmp_path / "bazos_variant.jpg"
    res = generate_media_variant(
        master_path=master_image_file,
        output_path=out_variant,
        profile=profile,
        role="COVER",
    )

    # 1. Output file exists and fits within byte limit
    assert res.file_bytes <= 500_000
    assert out_variant.stat().st_size <= 500_000

    # 2. Dimensions conform to max bounds
    assert res.width <= 1280
    assert res.height <= 960

    # 3. Preferred aspect ratio 4:3
    ratio = res.width / res.height
    assert abs(ratio - (4.0 / 3.0)) < 0.05


def test_variant_generation_fails_when_byte_limit_unreachable(master_image_file, tmp_path):
    """DoD: When limit is unreachable (e.g. 500 bytes max for large image), raises VariantGenerationError."""
    impossible_profile = MediaProfile(
        marketplace_key="impossible_mp",
        min_width=800,
        min_height=600,
        max_width=1920,
        max_height=1440,
        max_file_bytes=200,  # Impossible 200 bytes limit
        formats=["JPEG"],
    )

    out_variant = tmp_path / "impossible.jpg"
    with pytest.raises(VariantGenerationError) as exc_info:
        generate_media_variant(
            master_path=master_image_file,
            output_path=out_variant,
            profile=impossible_profile,
        )

    msg = str(exc_info.value)
    assert "Cannot compress variant" in msg
    assert "within limit of 200 bytes" in msg
    # File must not exist or be left oversized
    assert not out_variant.exists()
