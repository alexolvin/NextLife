"""Tests for Stage E0 (Каркас)."""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from nextlife.api.main import create_app
from nextlife.cli import http_client
from nextlife.cli.main import handle_cli
from nextlife.core.config import ConfigError, load_config
from nextlife.jobs.worker import run_worker
from nextlife.obs.truth_log import TruthAssertionError, record_truth
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent


def test_config_loading() -> None:
    cfg = load_config()
    assert cfg.app.base_currency == "EUR"
    assert "bazos_cz" in cfg.marketplaces
    assert "sbazar_cz" in cfg.marketplaces
    assert "aukro_cz" in cfg.marketplaces
    assert len(cfg.shipping.methods) > 0
    assert len(cfg.faq.patterns) > 0


def test_config_corruption_fails_with_exact_message(tmp_path: Path) -> None:
    # Create corrupted config file
    corrupt_cfg = tmp_path / "nextlife.yaml"
    corrupt_cfg.write_text(
        "code_release_days: 90\n", encoding="utf-8"
    )  # missing base_currency and other required keys

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir=tmp_path)

    err_str = str(exc_info.value)
    assert "Config validation error in file" in err_str
    assert "nextlife.yaml" in err_str
    assert "base_currency" in err_str or "server" in err_str


def test_cli_status_valid_json(live_server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI is a thin HTTP client (item 3): status comes from the live API."""
    monkeypatch.setenv("NEXTLIFE_BASE_URL", live_server_url)
    token = os.environ["NEXTLIFE_OPENCLAW_TOKEN"]
    _, status = http_client.request("GET", "/api/oc/status", token=token)
    assert isinstance(status, dict)
    assert "items_by_status" in status
    assert "jobs_in_queue" in status
    assert "connectors" in status
    assert "android_bridge" in status


def test_cli_execution(live_server_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("NEXTLIFE_BASE_URL", live_server_url)
    code = handle_cli(["status"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "items_by_status" in data


def test_api_service_app() -> None:
    app = create_app()
    assert app.title == "NextLife API"


def test_worker_service_single_pass() -> None:
    # Single pass run
    run_worker(once=True)


def test_audit_event_immutability() -> None:
    # Test that UPDATE on audit_event fails
    with get_db_session() as session:
        event = AuditEvent(
            actor="SYSTEM",
            action="TEST_INIT",
            entity="SYSTEM",
            entity_id="0",
            data={"init": True},
        )
        session.add(event)
        session.flush()

        event.action = "TAMPERED"
        with pytest.raises(DBAPIError) as exc_info:
            session.flush()
        assert "forbidden" in str(exc_info.value).lower()
        session.rollback()


def test_truth_log_requires_evidence() -> None:
    with get_db_session() as session:
        # Empty evidence must raise error
        with pytest.raises(TruthAssertionError):
            record_truth(session, claim="Item exists", recipient="OWNER", evidence={})

        # Valid evidence succeeds
        entry = record_truth(
            session,
            claim="Listing published",
            recipient="OWNER",
            evidence={"external_url": "https://example.com/test"},
        )
        assert entry.id is not None
