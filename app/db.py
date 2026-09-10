"""Database engine, session factory, and the declarative base."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

_is_sqlite = settings.database_url.startswith("sqlite")
_is_postgres = settings.database_url.startswith("postgresql")

_connect_args: dict = {}
if _is_sqlite:
    _connect_args["check_same_thread"] = False

_engine_kwargs: dict = {
    "echo": False,
    "future": True,
    "connect_args": _connect_args,
}
if _is_sqlite:
    # File-backed SQLite cannot share a small fixed pool across many parallel chart
    # requests; NullPool opens and closes per checkout so dashboards do not stall.
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_engine(settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _apply_sqlite_pragmas(cursor: sqlite3.Cursor) -> None:
    """Enable SQLite pragmas; fall back from WAL when sidecar files are corrupt."""
    cursor.execute("PRAGMA foreign_keys=ON")
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        logger.warning(
            "SQLite WAL mode unavailable (%s); falling back to DELETE journal mode. "
            "Stop the app and remove stale school.db-wal / school.db-shm if this persists.",
            exc,
        )
        cursor.execute("PRAGMA journal_mode=DELETE")


if _is_sqlite:

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            _apply_sqlite_pragmas(cursor)
        finally:
            cursor.close()


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _run_sqlite_migrations() -> None:
    """Apply pending Alembic revisions for local SQLite databases."""
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_cfg, "head")


def init_db() -> None:
    """Create any missing tables. Model modules must be imported first."""
    from app import models  # noqa: F401  (registers mappers on Base.metadata)
    from app.tenant.rls import apply_rls_policies, configure_rls

    configure_rls(engine)
    Base.metadata.create_all(bind=engine)
    if _is_sqlite:
        _run_sqlite_migrations()
    elif _is_postgres:
        with engine.begin() as connection:
            apply_rls_policies(connection)


def check_db_connection() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
