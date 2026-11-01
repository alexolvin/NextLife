import io
from pathlib import Path

from PIL import ExifTags, Image

from nextlife.media.ingest import ingest_original_image


def create_test_image_with_exif_and_gps() -> bytes:
    """Create a test JPEG containing Camera make/model and GPS metadata."""
    img = Image.new("RGB", (800, 600), color=(100, 150, 200))
    exif = img.getexif()

    # Orientation tag (0x0112)
    exif[0x0112] = 6  # Rotated 90 deg CW
    # Make tag (0x010F)
    exif[0x010F] = "Nikon"
    # Model tag (0x0110)
    exif[0x0110] = "Z6"
    # GPS Info IFD (0x8825) - coordinates
    gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps_ifd[1] = "N"
    gps_ifd[2] = 50.5

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_ingest_preserves_original_byte_for_byte(tmp_path):
    """DoD: Original image file is preserved byte-for-byte identical."""
    raw_bytes = create_test_image_with_exif_and_gps()

    result = ingest_original_image(
        raw_bytes=raw_bytes,
        storage_dir=tmp_path,
        original_filename="camera_shot.jpg",
    )

    # 1. Verify file content matches original exactly
    saved_bytes = Path(result.original_path).read_bytes()
    assert saved_bytes == raw_bytes
    assert len(saved_bytes) == result.file_bytes


def test_exif_stripped_from_published_and_gps_never_saved(tmp_path):
    """DoD: In published/sanitized files EXIF is completely absent; GPS is never saved in DB."""
    raw_bytes = create_test_image_with_exif_and_gps()

    result = ingest_original_image(
        raw_bytes=raw_bytes,
        storage_dir=tmp_path,
        original_filename="sample_gps.jpg",
    )

    # 1. GPS must NOT exist anywhere in extracted exif_data
    assert "GPSInfo" not in result.exif_data
    assert 0x8825 not in result.exif_data
    for k in result.exif_data.keys():
        assert "GPS" not in str(k)

    # Non-GPS tags are preserved in DB
    assert result.exif_data.get("Make") == "Nikon"
    assert result.orientation == 6

    # 2. In sanitized/published file, EXIF is strictly absent
    with Image.open(result.sanitized_path) as sanitized_img:
        exif_tags = sanitized_img.getexif()
        assert len(exif_tags) == 0
