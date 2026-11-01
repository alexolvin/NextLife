"""Database engine and session management for NextLife."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from nextlife.core.config import ConfigBundle, get_database_url, load_config

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def reset_engine() -> None:
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionFactory = None


def get_engine(config: ConfigBundle | None = None) -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        cfg = config or load_config()
        raw_dsn = get_database_url(cfg)

        _engine = create_engine(
            raw_dsn,
            pool_pre_ping=True,
        )
        _SessionFactory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


@contextmanager
def get_db_session(config: ConfigBundle | None = None) -> Generator[Session, None, None]:
    get_engine(config)
    assert _SessionFactory is not None
    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
