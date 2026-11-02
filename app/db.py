"""Database engine, session factory, and the declarative base."""

from __future__ import annotations

import logging
import os
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

    @event.listens_for(engine, "connect")
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


def reset_dev_schema() -> None:
    """Drop ORM tables and Alembic stamp, then re-apply migrations (SQLite dev/seed)."""
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    if _is_sqlite:
        _run_sqlite_migrations()


def startup_ddl_enabled() -> bool:
    """Whether ``init_db`` may apply schema migrations on SQLite.

    PostgreSQL and other production targets must be migrated with
    ``alembic upgrade head`` before the app starts. ``init_db`` still attaches
    runtime RLS session hooks on Postgres but does not create tables or policies.
    Set ``SKIP_STARTUP_DDL=1`` to skip Alembic on SQLite (e.g. tests that
    manage migrations explicitly).
    """
    explicit = os.environ.get("SKIP_STARTUP_DDL", "").strip().lower()
    if explicit in {"1", "true", "yes"}:
        return False
    if _is_postgres:
        return False
    return True


def init_db() -> None:
    """Prepare the database for runtime. Model modules must be imported first."""
    from app import models  # noqa: F401  (registers mappers on Base.metadata)
    from app.tenant.rls import configure_rls

    configure_rls(engine)
    if not startup_ddl_enabled():
        return
    if _is_sqlite:
        _run_sqlite_migrations()


def check_db_connection() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _alembic_script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(cfg)


def check_db_migrations_at_head(conn_engine: Engine | None = None) -> bool:
    """Return True when the database revision matches an Alembic head."""
    from alembic.runtime.migration import MigrationContext

    heads = set(_alembic_script_directory().get_heads())
    if not heads:
        return False

    db_engine = conn_engine or engine
    try:
        with db_engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current = context.get_current_revision()
    except Exception:
        return False

    return current is not None and current in heads


def db_readiness() -> dict[str, str]:
    """Database connectivity and migration revision state for ``/readyz``."""
    if not check_db_connection():
        return {"database": "unavailable", "migrations": "unavailable"}
    migrations = "ok" if check_db_migrations_at_head() else "pending"
    return {"database": "ok", "migrations": migrations}
