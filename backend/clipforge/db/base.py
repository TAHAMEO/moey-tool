"""SQLAlchemy engine and session helpers (synchronous, psycopg 3)."""

from __future__ import annotations

import contextlib
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


_lock = threading.Lock()
_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker[Session]] = {}


def _build_engine(url: str) -> Engine:
    engine = create_engine(
        url,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):  # pragma: no cover - trivial
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")
        dbapi_connection.commit()

    return engine


def get_engine() -> Engine:
    url = get_settings().database_url
    with _lock:
        engine = _engines.get(url)
        if engine is None:
            engine = _engines[url] = _build_engine(url)
        return engine


def session_factory() -> sessionmaker[Session]:
    url = get_settings().database_url
    with _lock:
        factory = _factories.get(url)
    if factory is None:
        engine = get_engine()
        with _lock:
            factory = _factories.setdefault(url, sessionmaker(bind=engine, expire_on_commit=False))
    return factory


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one transaction per request."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engines() -> None:
    """Dispose all pooled connections (tests, forked workers)."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _factories.clear()
