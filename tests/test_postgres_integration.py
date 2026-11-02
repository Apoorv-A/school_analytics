"""PostgreSQL integration tests (migrations, RLS, runtime role).

These tests require a real PostgreSQL server. Default CI (SQLite-only preflight)
skips them when ``POSTGRES_TEST_DATABASE_URL`` is unset.

Local run
---------
1. Start Postgres, e.g.::

     docker compose -f deploy/docker-compose.yml up -d postgres

2. Install the driver (not required for SQLite CI)::

     pip install 'psycopg[binary]'

3. Point at a throwaway database (tests reset the ``public`` schema each run)::

     export POSTGRES_TEST_DATABASE_URL='postgresql+psycopg://school:school@localhost:5432/school_analytics'

4. Run only the Postgres suite::

     pytest tests/test_postgres_integration.py -v -m postgres

Use ``POSTGRES_TEST_DATABASE_URL`` instead of ``DATABASE_URL`` so the main test
suite keeps its isolated SQLite file from ``tests/conftest.py``.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import quote_plus, urlparse, urlunparse

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import PROJECT_ROOT
from app.db import (
    check_db_connection,
    check_db_migrations_at_head,
    init_db,
    startup_ddl_enabled,
)
from app.models import School, Tenant

pytestmark = pytest.mark.postgres

_APP_ROLE = "school_analytics_app_test"
_APP_ROLE_PASSWORD = "school-analytics-app-test"


def _postgres_url() -> str | None:
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL", "").strip()
    if url.startswith("postgresql"):
        return url
    return None


@pytest.fixture(scope="module")
def postgres_url() -> str:
    pytest.importorskip("psycopg", reason="install psycopg[binary] for PostgreSQL tests")
    url = _postgres_url()
    if url is None:
        pytest.skip(
            "POSTGRES_TEST_DATABASE_URL is unset; export a postgresql:// or "
            "postgresql+psycopg:// URL (see module docstring)"
        )
    probe = create_engine(url, pool_pre_ping=True)
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment-specific
        pytest.skip(
            f"Cannot connect to PostgreSQL at {url!r}: {type(exc).__name__}: {exc}"
        )
    else:
        probe.dispose()
    return url


@pytest.fixture
def owner_engine(postgres_url: str) -> Engine:
    engine = create_engine(postgres_url, pool_pre_ping=True)
    _reset_public_schema(engine)
    yield engine
    engine.dispose()


def _reset_public_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO PUBLIC"))


def _run_alembic_upgrade(url: str, revision: str = "head") -> None:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, revision)


def _app_role_url(owner_url: str) -> str:
    parsed = urlparse(owner_url)
    username = quote_plus(_APP_ROLE)
    password = quote_plus(_APP_ROLE_PASSWORD)
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    database = parsed.path or "/school_analytics"
    scheme = parsed.scheme
    return urlunparse((scheme, f"{username}:{password}@{host}{port}", database, "", "", ""))


def _provision_app_role(owner_engine: Engine) -> str:
    db_name = owner_engine.url.database or "school_analytics"
    # CREATE ROLE PASSWORD does not accept bind parameters in PostgreSQL.
    escaped_password = _APP_ROLE_PASSWORD.replace("'", "''")
    with owner_engine.begin() as conn:
        if conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": _APP_ROLE},
        ).scalar():
            conn.execute(text(f"DROP OWNED BY {_APP_ROLE}"))
        conn.execute(text(f"DROP ROLE IF EXISTS {_APP_ROLE}"))
        conn.execute(
            text(
                f"CREATE ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{escaped_password}' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
        conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {_APP_ROLE}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        conn.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}"
            )
        )
        conn.execute(
            text(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"
            )
        )
    return _app_role_url(str(owner_engine.url))


def _seed_two_tenant_schools(owner_engine: Engine) -> tuple[uuid.UUID, uuid.UUID, int, int]:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    with Session(owner_engine) as session:
        session.add(
            Tenant(id=tenant_a, key="pg-a", display_name="Postgres Tenant A")
        )
        session.add(
            Tenant(id=tenant_b, key="pg-b", display_name="Postgres Tenant B")
        )
        session.flush()
        school_a = School(tenant_id=tenant_a, name="School A", city="Pune", board="CBSE")
        school_b = School(tenant_id=tenant_b, name="School B", city="Mumbai", board="ICSE")
        session.add_all([school_a, school_b])
        session.commit()
        return tenant_a, tenant_b, school_a.id, school_b.id


def _set_tenant(conn, tenant_id: uuid.UUID) -> None:
    conn.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


class TestPostgresMigrations:
    def test_alembic_upgrade_head_on_clean_database(self, owner_engine, postgres_url):
        _run_alembic_upgrade(postgres_url, "head")
        tables = set(inspect(owner_engine).get_table_names())
        assert "alembic_version" in tables
        assert {
            "tenants",
            "schools",
            "users",
            "students",
            "attendance",
            "intervention_cases",
            "security_audit_events",
        }.issubset(tables)
        with owner_engine.connect() as conn:
            revision = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == "014_parental_consent"


class TestPostgresRlsIsolation:
    def test_cross_tenant_rows_hidden_under_rls(self, owner_engine, postgres_url):
        _run_alembic_upgrade(postgres_url, "head")
        tenant_a, tenant_b, school_a_id, school_b_id = _seed_two_tenant_schools(
            owner_engine
        )
        app_url = _provision_app_role(owner_engine)
        app_engine = create_engine(app_url, pool_pre_ping=True)
        try:
            with app_engine.connect() as conn:
                _set_tenant(conn, tenant_a)
                visible = conn.execute(
                    text("SELECT id, tenant_id FROM schools ORDER BY id")
                ).all()
                assert visible == [(school_a_id, tenant_a)]

                _set_tenant(conn, tenant_b)
                visible_b = conn.execute(
                    text("SELECT id, tenant_id FROM schools ORDER BY id")
                ).all()
                assert visible_b == [(school_b_id, tenant_b)]

                _set_tenant(conn, tenant_a)
                blocked = conn.execute(
                    text("SELECT id FROM schools WHERE id = :sid"),
                    {"sid": school_b_id},
                ).all()
                assert blocked == []
        finally:
            app_engine.dispose()

    def test_orm_query_respects_tenant_session_variable(
        self, owner_engine, postgres_url
    ):
        _run_alembic_upgrade(postgres_url, "head")
        tenant_a, tenant_b, school_a_id, _school_b_id = _seed_two_tenant_schools(
            owner_engine
        )
        app_url = _provision_app_role(owner_engine)
        app_engine = create_engine(app_url, pool_pre_ping=True)
        session_factory = sessionmaker(bind=app_engine, autoflush=False)
        try:
            with session_factory() as session:
                with session.begin():
                    _set_tenant(session.connection(), tenant_a)
                    rows = session.scalars(select(School).order_by(School.id)).all()
                    assert [row.id for row in rows] == [school_a_id]
                    assert all(row.tenant_id == tenant_a for row in rows)

                with session.begin():
                    _set_tenant(session.connection(), tenant_b)
                    rows_b = session.scalars(select(School).order_by(School.id)).all()
                    assert len(rows_b) == 1
                    assert rows_b[0].tenant_id == tenant_b
        finally:
            app_engine.dispose()


class TestPostgresRuntimeRole:
    def test_app_role_starts_without_startup_ddl(
        self, owner_engine, postgres_url, monkeypatch
    ):
        _run_alembic_upgrade(postgres_url, "head")
        app_url = _provision_app_role(owner_engine)
        app_engine = create_engine(app_url, pool_pre_ping=True)

        monkeypatch.setattr("app.db._is_postgres", True)
        monkeypatch.setattr("app.db.settings.database_url", app_url)
        monkeypatch.setattr("app.db.engine", app_engine)
        monkeypatch.delenv("SKIP_STARTUP_DDL", raising=False)

        assert startup_ddl_enabled() is False
        init_db()
        assert check_db_connection() is True
        assert check_db_migrations_at_head(app_engine) is True
        app_engine.dispose()
