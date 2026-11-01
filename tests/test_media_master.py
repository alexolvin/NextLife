import pytest
from PIL import Image

from nextlife.media.master import generate_master_image


@pytest.fixture
def test_image_path(tmp_path):
    img_path = tmp_path / "test_orig.png"
    img = Image.new("RGB", (1000, 800), color=(200, 100, 50))
    # Draw some pixels to make it non-trivial
    for x in range(100, 300):
        for y in range(100, 300):
            img.putpixel((x, y), (50, 150, 250))
    img.save(img_path, format="PNG")
    return img_path


def test_master_generation_determinism(test_image_path, tmp_path):
    """DoD: Repeat run gives byte-for-byte identical output with same parameters."""
    out_1 = tmp_path / "master_run1.png"
    out_2 = tmp_path / "master_run2.png"

    crop = (50, 50, 600, 500)
    res_1 = generate_master_image(
        original_path=test_image_path,
        output_path=out_1,
        crop_box=crop,
        rotation_angle_deg=5.0,
        normalize_contrast=True,
    )

    res_2 = generate_master_image(
        original_path=test_image_path,
        output_path=out_2,
        crop_box=crop,
        rotation_angle_deg=5.0,
        normalize_contrast=True,
    )

    # 1. Hashes must be strictly identical
    assert res_1.sha256 == res_2.sha256
    assert res_1.file_bytes == res_2.file_bytes
    assert res_1.width == res_2.width == (600 - 50)
    assert res_1.height == res_2.height == (500 - 50)

    # 2. Files must be byte-for-byte identical
    bytes_1 = out_1.read_bytes()
    bytes_2 = out_2.read_bytes()
    assert bytes_1 == bytes_2
