"""Pytest fixtures and configuration."""

import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

# The single test DSN: set by conftest for the test process. An externally
# exported NEXTLIFE_DSN (selfcheck/acceptance) takes precedence.
TEST_DSN = "postgresql+psycopg2://nextlife_dev@/nextlife_test?host=/var/run/postgresql"

ROOT_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def setup_test_env() -> None:
    dsn = os.environ.get("NEXTLIFE_DSN") or TEST_DSN
    os.environ["NEXTLIFE_DSN"] = dsn

    # РУБЕЖЬ 1 — предохранитель ДО подключения (pre-connection fuse).
    # Имя базы берётся из адреса через make_url(dsn).database. Нетестовый адрес
    # отклоняется ДО создания движка: к запрещённой базе не выполняется ни
    # одной попытки подключения (нет ни сокета, ни ошибки драйвера).
    from sqlalchemy.engine import make_url

    dsn_db = make_url(dsn).database or ""
    if not dsn_db.endswith("_test"):
        raise RuntimeError(
            f"Test database guard refusal: database '{dsn_db}' in the DSN does not end with '_test'. "
            "Tests are strictly forbidden from connecting to non-test databases. "
            "Refused before any connection attempt."
        )

    # РУБЕЖЬ 2 — сверка ПОСЛЕ подключения (защита в глубину).
    # Живое соединение должно реально находиться на базе, которую называет DSN.
    from sqlalchemy import text

    from nextlife.storage.db import get_engine

    try:
        with get_engine().connect() as conn:
            row = conn.execute(text("SELECT current_database();")).fetchone()
        db_name = row[0] if row else ""
    except Exception as exc:
        if os.environ.get("NEXTLIFE_SKIP_DB_ON_ERROR") == "1":
            if not os.environ.get("NEXTLIFE_WEB_TOKEN"):
                os.environ["NEXTLIFE_WEB_TOKEN"] = "test_token_secret"
            return
        raise RuntimeError(f"Failed to connect to test database: {exc}") from exc

    if not db_name.endswith("_test"):
        raise RuntimeError(
            f"Test database guard refusal: current database '{db_name}' does not end with '_test'. "
            "Tests are strictly forbidden from connecting to non-test databases."
        )

    # The Owner keeps nextlife_test empty: apply migrations from scratch
    # (idempotent — a no-op when the schema is already at head).
    from alembic import command
    from alembic.config import Config as AlembicConfig

    command.upgrade(AlembicConfig(str(ROOT_DIR / "alembic.ini")), "head")

    from nextlife.connectors.registry import sync_marketplaces
    from nextlife.core.config import load_config
    from nextlife.storage.db import get_db_session

    with get_db_session() as session:
        sync_marketplaces(session, load_config())
        session.commit()

    if not os.environ.get("NEXTLIFE_WEB_TOKEN"):
        os.environ["NEXTLIFE_WEB_TOKEN"] = "test_token_secret"

    # Item 3: the OpenClaw skill token must be resolvable for the /api/oc/*
    # namespace. The process env takes precedence over .env (resolve_secret),
    # so a test-run value never reads the owner's real token.
    if not os.environ.get("NEXTLIFE_OPENCLAW_TOKEN"):
        os.environ["NEXTLIFE_OPENCLAW_TOKEN"] = "test_oc_token"


@pytest.fixture(scope="session")
def live_server_url():
    """Spin up isolated uvicorn test server bound to test database for playwright tests."""
    import socket
    import subprocess
    import sys
    import time
    from urllib.request import Request, urlopen

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # NEXTLIFE_DSN is already in os.environ (set by the session fixture).
    env = dict(os.environ)
    env["NEXTLIFE_PORT"] = str(port)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "nextlife.api.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            with urlopen(Request(f"{url}/health"), timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.1)

    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()



def clean_database(session: "Session") -> None:
    from nextlife.media.models import MediaMaster, MediaOriginal, MediaVariant
    from nextlife.pricing.history import PriceHistory
    from nextlife.storage.models import (
        Approval,
        BridgeDailyUsage,
        BridgeLock,
        ChannelCanary,
        BuyCandidate,
        BuyRequest,
        Deal,
        DeliveryOption,
        Handover,
        Item,
        ItemDefect,
        ItemPhoto,
        Job,
        Listing,
        Lot,
        Message,
        Outbox,
        PaymentOption,
        ResearchResult,
        ResearchRun,
        Settlement,
        Thread,
        TruthLog,
    )

    # Children before parents (FK-safe order); all transactional tables.
    session.query(ChannelCanary).delete()
    session.query(MediaVariant).delete()
    session.query(MediaMaster).delete()
    session.query(MediaOriginal).delete()
    session.query(PriceHistory).delete()
    session.query(ItemDefect).delete()
    session.query(DeliveryOption).delete()
    session.query(TruthLog).delete()
    session.query(Message).delete()
    session.query(Outbox).delete()
    session.query(Thread).delete()
    session.query(Deal).delete()
    session.query(Handover).delete()
    session.query(Settlement).delete()
    session.query(Listing).delete()
    session.query(Job).delete()
    session.query(BuyCandidate).delete()
    session.query(BuyRequest).delete()
    session.query(ResearchResult).delete()
    session.query(ResearchRun).delete()
    session.query(Approval).delete()
    session.query(ItemPhoto).delete()
    session.query(PaymentOption).delete()
    session.query(Lot).delete()
    session.query(Item).delete()
    session.query(BridgeDailyUsage).delete()
    # Free a stale bridge lock (crashed holder) so tests never deadlock.
    session.query(BridgeLock).filter(BridgeLock.locked_by.isnot(None)).update(
        {"locked_by": None, "locked_at": None}
    )
    session.commit()


