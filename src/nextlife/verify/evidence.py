"""Append-only evidence registry (TZ Connectors 2.4).

Files are written once. A second write of the same relative name raises.
``manifest.json`` is rewritten only by ``finalize`` and lists sha256 of
every other file. Nothing in this module deletes or truncates evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class EvidenceExistsError(FileExistsError):
    """Raised when a write would overwrite an evidence file."""


class EvidenceRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run_dir(self, platform: str, run_id: str) -> Path:
        directory = self.root / platform / run_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_bytes(self, platform: str, run_id: str, name: str, data: bytes) -> Path:
        path = self._path(platform, run_id, name)
        if path.exists():
            raise EvidenceExistsError(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_text(self, platform: str, run_id: str, name: str, text: str) -> Path:
        return self.write_bytes(platform, run_id, name, text.encode("utf-8"))

    def write_json(self, platform: str, run_id: str, name: str, payload: Any) -> Path:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return self.write_text(platform, run_id, name, text)

    def finalize(self, platform: str, run_id: str) -> Path:
        directory = self.run_dir(platform, run_id)
        manifest: dict[str, str] = {}
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            rel = str(path.relative_to(directory))
            manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest_path

    def _path(self, platform: str, run_id: str, name: str) -> Path:
        if name == "manifest.json" or ".." in Path(name).parts:
            raise ValueError(f"refusing evidence name {name!r}")
        return self.run_dir(platform, run_id) / name
