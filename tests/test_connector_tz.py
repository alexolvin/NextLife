"""Contract tests for the connector TZ pieces that do not touch a live site."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from PIL import Image

from nextlife.android.lease_scheduler import LeaseScheduler
from nextlife.android.photo_pipeline import AndroidPhotoPipeline, MediaStoreIndexError
from nextlife.connectors.ops import publish
from nextlife.services.category_gate import category_rejection, eligible_publish_keys
from nextlife.services.facebook_pack import build_zip, clipboard_text


class _Item:
    category_key = "electronics"


class _Session:
    def get(self, model, key):
        return _Item() if str(key) == "130" else None

    def scalar(self, *args, **kwargs):
        return None

    def rollback(self):
        return None


def test_category_gate_rejects_gpu_for_books_and_baby():
    books = SimpleNamespace(
        key="trhknih_cz",
        title="TrhKnih.cz",
        allowed_categories=["books"],
        filter_reason="только книги",
        rules_evidence="https://www.trhknih.cz/",
    )
    baby = SimpleNamespace(
        key="mimibazar_cz",
        title="Mimibazar.cz",
        allowed_categories=["baby", "kids"],
        filter_reason="только детские товары",
        rules_evidence="",
    )
    open_market = SimpleNamespace(key="bazos_cz", title="Bazoš", allowed_categories=None, filter_reason=None, rules_evidence=None)
    assert "книги" in category_rejection(books, "electronics")
    assert "Доказательство" in category_rejection(books, "electronics")
    assert category_rejection(baby, "electronics")
    assert category_rejection(open_market, "electronics") is None
    config = SimpleNamespace(marketplaces={"trhknih_cz": books, "bazos_cz": open_market, "sbazar_cz": open_market})
    assert eligible_publish_keys(config, "electronics", ["trhknih_cz", "sbazar_cz", "bazos_cz"]) == ["bazos_cz", "sbazar_cz"]


def test_ops_blocks_android_when_broker_is_down(monkeypatch):
    monkeypatch.setattr("nextlife.connectors.ops.broker_reachable", lambda *args, **kwargs: False)
    result = publish(_Session(), "130", "bazos_cz")
    assert result.status == "BLOCKED"
    assert "8090" in str(result.evidence.get("reason"))


def test_ops_category_filter_is_not_allowed(monkeypatch):
    monkeypatch.setattr("nextlife.connectors.ops.broker_reachable", lambda *args, **kwargs: True)
    result = publish(_Session(), "130", "mimibazar_cz")
    assert result.status == "NOT_ALLOWED"
    assert result.evidence.get("reason")


def test_facebook_pack_has_lot_text_and_no_storage_note(tmp_path: Path):
    text = clipboard_text(
        title="ASUS TUF Gaming AMD Radeon RX 7900 XTX 24 GB",
        price="26000.00",
        currency="CZK",
        description="Prodám grafickou kartu AMD Radeon RX 7900 XTX s 24 GB paměti.",
    )
    assert "RX 7900 XTX" in text
    assert "26000.00 CZK" in text
    assert "Кабинет" not in text
    photo = tmp_path / "shot.jpg"
    Image.new("RGB", (8, 8), "red").save(photo)
    dest = tmp_path / "pack.zip"
    build_zip(dest, title="ASUS", price="26000.00", currency="CZK", description=text, photo_paths=[photo])
    with ZipFile(dest) as archive:
        names = archive.namelist()
        body = archive.read("facebook.txt").decode("utf-8")
    assert "facebook.txt" in names
    assert any(name.startswith("photos/") for name in names)
    assert "Кабинет" not in body


def test_scheduler_skips_platform_in_cooldown():
    now = datetime(2026, 11, 5, 2, 0, 0)
    scheduler = LeaseScheduler()
    scheduler.note_cooldown("sbazar_cz", 300, now)
    assert scheduler.can_lease("bazos_cz", now + timedelta(seconds=10))
    assert not scheduler.can_lease("sbazar_cz", now + timedelta(seconds=10))
    assert scheduler.ready_platforms(["sbazar_cz", "bazos_cz"], now + timedelta(seconds=10)) == ["bazos_cz"]


def test_photo_pipeline_refuses_missing_adb_and_uses_lot_names(tmp_path: Path):
    pipeline = AndroidPhotoPipeline(adb_runner=None)
    with pytest.raises(MediaStoreIndexError):
        pipeline.push_lot_photos(130, [])
    calls = []

    def runner(args):
        calls.append(args)
        if "content" in args:
            return "Row: 0 _display_name=nl130_01.jpg relative_path=Pictures/NL130/\n"
        return ""

    photo = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "blue").save(photo)
    named = AndroidPhotoPipeline(adb_runner=runner)
    pushed = named.push_lot_photos(130, [photo])
    assert pushed == ["/sdcard/Pictures/NL130/nl130_01.jpg"]
    assert any(args[:2] == ["push", str(photo)] for args in calls)


def test_report_empty_registry_has_no_live_status(monkeypatch, tmp_path: Path):
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "connector_report.py"
    spec = importlib.util.spec_from_file_location("connector_report_under_test", path)
    report = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(report)
    monkeypatch.setattr(report, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(report, "_db_rows", lambda: [])
    text = report.render("")
    assert "LIVE_VERIFIED" not in text
    assert "готово" not in text.casefold()
    assert "| bazos_cz | NOT_STARTED |" in text
    assert "| vinted | NOT_STARTED |" in text


def test_report_keeps_old_incidents_and_latest_unchecked(monkeypatch, tmp_path: Path):
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "connector_report.py"
    spec = importlib.util.spec_from_file_location("connector_report_latest", path)
    report = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(report)
    monkeypatch.setattr(report, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(report, "_db_rows", lambda: [])
    old = tmp_path / "evidence" / "stage0" / "20200101T000000Z"
    new = tmp_path / "evidence" / "stage0" / "20260101T000000Z"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "not_checked.json").write_text(json.dumps([{"text": "STAROY"}]), encoding="utf-8")
    (new / "not_checked.json").write_text(json.dumps([{"text": "NOVYJ"}]), encoding="utf-8")
    (old / "incidents.json").write_text(json.dumps([{"text": "INCYDENT"}]), encoding="utf-8")
    text = report.render("")
    assert "NOVYJ" in text
    assert "STAROY" not in text
    assert "INCYDENT" in text
