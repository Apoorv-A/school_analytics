"""Database engine, session factory, and the declarative base."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

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


if _is_sqlite:

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
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


def init_db() -> None:
    """Create any missing tables. Model modules must be imported first."""
    from app import models  # noqa: F401  (registers mappers on Base.metadata)
    from app.tenant.rls import apply_rls_policies, configure_rls

    configure_rls(engine)
    Base.metadata.create_all(bind=engine)
    if _is_postgres:
        with engine.begin() as connection:
            apply_rls_policies(connection)


def check_db_connection() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
