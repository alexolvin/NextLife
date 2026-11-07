"""Android Photo Pipeline for marketplace publishing (TZ - Konnectory.md, Section 5.1).

Handles:
1. Pushing lot photos to /sdcard/Pictures/NL<lot_id>/nl<lot_id>_XX.jpg.
2. Triggering media scanner broadcast and verifying via `content query`.
3. Pre-submit thumbnail pHash verification (Hamming distance <= 12).
4. Cleaning up device directory after session.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image

logger = logging.getLogger(__name__)


class PhotoMismatchError(Exception):
    """Raised when selected photos on device do not match expected lot photos."""


class MediaStoreIndexError(Exception):
    """Raised when MediaStore indexing fails to register all pushed photos."""


class AndroidPhotoPipeline:
    def __init__(self, adb_runner=None):
        self.adb = adb_runner

    def push_lot_photos(self, lot_id: str | int, photo_paths: list[str | Path]) -> list[str]:
        """Pushes photos to /sdcard/Pictures/NL<lot_id>/ and verifies MediaStore indexing."""
        lot_tag = f"NL{lot_id}"
        dest_dir = f"/sdcard/Pictures/{lot_tag}"

        # Clean any old directory first
        self.cleanup(lot_id)

        # Create directory
        self._adb_cmd(["shell", "mkdir", "-p", dest_dir])

        device_files = []
        for idx, src_p in enumerate(photo_paths, start=1):
            src_path = Path(src_p)
            if not src_path.exists():
                continue
            dest_file = f"{dest_dir}/nl{lot_id}_{idx:02d}.jpg"
            self._adb_cmd(["push", str(src_path), dest_file])
            # Trigger media scanner
            self._adb_cmd([
                "shell", "am", "broadcast",
                "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", f"file://{dest_file}",
            ])
            device_files.append(dest_file)

        # Wait for MediaStore indexing and verify factually
        expected_count = len(device_files)
        indexed_count = 0
        for _ in range(10):
            out = self._adb_cmd([
                "shell", "content", "query",
                "--uri", "content://media/external/images/media",
                "--projection", "_display_name:relative_path",
                "--where", f"relative_path='Pictures/{lot_tag}/'",
            ])
            lines = [l for l in out.strip().splitlines() if "Row:" in l]
            if len(lines) == expected_count:
                indexed_count = len(lines)
                break
            time.sleep(1)

        if indexed_count != expected_count:
            raise MediaStoreIndexError(
                f"MediaStore indexed {indexed_count}/{expected_count} photos in Pictures/{lot_tag}/"
            )

        logger.info("Successfully pushed and indexed %d photos for lot %s", expected_count, lot_id)
        return device_files

    def verify_presubmit_thumbnails(
        self,
        screenshot_path: Path | str,
        thumbnail_boxes: list[tuple[int, int, int, int]],
        original_photo_paths: list[str | Path],
        max_hamming_dist: int = 12,
    ) -> bool:
        """Compares pHash of thumbnails on screen against center-cropped originals."""
        orig_hashes = []
        for op in original_photo_paths:
            with Image.open(op) as im:
                # Center crop to square
                w, h = im.size
                min_dim = min(w, h)
                left = (w - min_dim) // 2
                top = (h - min_dim) // 2
                crop = im.crop((left, top, left + min_dim, top + min_dim))
                orig_hashes.append(imagehash.phash(crop.convert("RGB")))

        with Image.open(screenshot_path) as screen:
            for idx, box in enumerate(thumbnail_boxes):
                thumb = screen.crop(box)
                th_hash = imagehash.phash(thumb.convert("RGB"))
                min_d = min(th_hash - oh for oh in orig_hashes) if orig_hashes else 999
                if min_d > max_hamming_dist:
                    raise PhotoMismatchError(
                        f"Thumbnail #{idx} at {box} has min distance {min_d} > {max_hamming_dist} to originals"
                    )

        return True

    def cleanup(self, lot_id: str | int) -> None:
        """Removes the lot photo directory and triggers MediaStore cleanup."""
        lot_tag = f"NL{lot_id}"
        dest_dir = f"/sdcard/Pictures/{lot_tag}"
        self._adb_cmd(["shell", "rm", "-rf", dest_dir])
        # Rescan to remove from MediaStore
        self._adb_cmd([
            "shell", "am", "broadcast",
            "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d", f"file://{dest_dir}",
        ])

    def _adb_cmd(self, args: list[str]) -> str:
        if self.adb is None:
            raise MediaStoreIndexError(
                "ADB runner is not configured. Photo push goes through PhoneBroker, not a hardcoded device serial."
            )
        return self.adb(args)
