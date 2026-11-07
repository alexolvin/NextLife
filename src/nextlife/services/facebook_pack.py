"""Manual Facebook package (TZ Connectors 6.7).

Automation of Facebook is forbidden. This module only builds the text
the owner copies and a zip of the lot's publish photos.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def clipboard_text(*, title: str, price: str, currency: str, description: str) -> str:
    return (
        f"{title.strip()}\n"
        f"{price} {currency}\n\n"
        f"{description.strip()}\n"
    )


def build_zip(
    dest: Path,
    *,
    title: str,
    price: str,
    currency: str,
    description: str,
    photo_paths: list[Path],
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = clipboard_text(title=title, price=price, currency=currency, description=description)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("facebook.txt", text)
        for index, path in enumerate(photo_paths, start=1):
            try:
                readable = path.is_file()
            except OSError:
                readable = False
            if readable:
                archive.write(path, arcname=f"photos/{index:02d}{path.suffix.lower() or '.jpg'}")
    return dest
