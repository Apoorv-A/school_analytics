"""Alembic migration architecture tests (P0-1).

Validates immutable baseline DDL, full upgrade paths, and schema parity with the ORM
at head. SQLite is used for fast CI; PostgreSQL coverage lives in
``tests/test_postgres_integration.py``.
"""

from __future__ import annotations

import ast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app import models  # noqa: F401
from app.config import PROJECT_ROOT
from app.db import Base, check_db_migrations_at_head

BASELINE_PATH = PROJECT_ROOT / "alembic/versions/001_baseline_multi_tenant.py"
HEAD_REVISION = "017_remark_visibility"

CORE_TABLES = frozenset(
    {
        "tenants",
        "tenant_domains",
        "schools",
        "academic_years",
        "terms",
        "grades",
        "sections",
        "subjects",
        "users",
        "teachers",
        "students",
        "teacher_assignments",
        "assessments",
        "scores",
        "attendance",
        "remarks",
    }
)

HEAD_TABLES = CORE_TABLES | {
    "import_runs",
    "import_sync_state",
    "import_conflicts",
    "intervention_cases",
    "intervention_contacts",
    "intervention_events",
    "action_tasks",
    "parental_consent_records",
    "password_reset_tokens",
    "security_audit_events",
    "import_mapping_queue",
}


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def _column_names(engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


def _fk_targets(engine, table: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for fk in inspect(engine).get_foreign_keys(table):
        for local_col, _remote in zip(
            fk["constrained_columns"],
            fk["referred_columns"],
            strict=False,
        ):
            out.add((local_col, fk["referred_table"]))
    return out


class TestBaselineMigrationImmutability:
    def test_baseline_does_not_import_live_orm(self):
        source = BASELINE_PATH.read_text()
        tree = ast.parse(source)
        imports = {
            node.names[0].name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "app.models" not in imports
        assert "app.db" not in imports
        assert "Base.metadata" not in source
        assert "create_all" not in source

    def test_baseline_uses_explicit_create_table(self):
        source = BASELINE_PATH.read_text()
        assert "op.create_table(" in source
        assert source.count("op.create_table(") == len(CORE_TABLES)


class TestMigrationUpgradePaths:
    def test_upgrade_zero_to_head(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'fresh.db'}"
        command.upgrade(_alembic_cfg(url), "head")
        engine = create_engine(url)
        try:
            tables = _table_names(engine)
            assert HEAD_TABLES.issubset(tables)
            with engine.connect() as conn:
                revision = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            assert revision == HEAD_REVISION
            assert check_db_migrations_at_head(engine) is True
        finally:
            engine.dispose()

    def test_upgrade_from_previous_revision_to_head(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'step.db'}"
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "009_security_audit_events")
        engine = create_engine(url)
        try:
            assert "import_mapping_queue" not in _table_names(engine)
            assert "security_audit_events" in _table_names(engine)
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")
        engine = create_engine(url)
        try:
            assert "import_mapping_queue" in _table_names(engine)
            revision = engine.connect().execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == HEAD_REVISION
        finally:
            engine.dispose()

    def test_rerun_head_is_idempotent(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'idempotent.db'}"
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")
        engine = create_engine(url)
        try:
            before = {
                table: _column_names(engine, table)
                for table in sorted(_table_names(engine))
            }
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")
        engine = create_engine(url)
        try:
            after = {
                table: _column_names(engine, table)
                for table in sorted(_table_names(engine))
            }
            assert before == after
        finally:
            engine.dispose()


class TestHeadSchemaExpectations:
    @pytest.fixture()
    def migrated_engine(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'schema.db'}"
        command.upgrade(_alembic_cfg(url), "head")
        engine = create_engine(url)
        yield engine
        engine.dispose()

    def test_users_has_mfa_and_lifecycle_columns(self, migrated_engine):
        cols = _column_names(migrated_engine, "users")
        assert {
            "external_id",
            "source_system",
            "totp_secret_enc",
            "totp_pending_secret_enc",
            "mfa_enrolled_at",
            "session_version",
            "must_change_password",
        }.issubset(cols)

    def test_user_mfa_recovery_codes_table(self, migrated_engine):
        cols = _column_names(migrated_engine, "user_mfa_recovery_codes")
        assert {
            "tenant_id",
            "user_id",
            "code_hash",
            "used_at",
            "created_at",
        }.issubset(cols)

    def test_students_external_correlation_columns(self, migrated_engine):
        cols = _column_names(migrated_engine, "students")
        assert {"external_id", "source_system"}.issubset(cols)

    def test_intervention_unique_constraints(self, migrated_engine):
        insp = inspect(migrated_engine)
        uniques = {u["name"] for u in insp.get_unique_constraints("import_mapping_queue")}
        assert "uq_import_mapping_pending_key" in uniques

    def test_core_foreign_keys_present(self, migrated_engine):
        schools_fks = _fk_targets(migrated_engine, "schools")
        assert ("tenant_id", "tenants") in schools_fks
        students_fks = _fk_targets(migrated_engine, "students")
        assert ("section_id", "sections") in students_fks

    def test_head_schema_matches_orm_metadata(self, migrated_engine):
        orm_engine = create_engine(
            f"sqlite:///{migrated_engine.url.database}-orm.db"
        )
        Base.metadata.create_all(orm_engine)
        mig_tables = _table_names(migrated_engine)
        orm_tables = _table_names(orm_engine)
        assert mig_tables == orm_tables

        mismatches: list[str] = []
        for table in sorted(mig_tables):
            mig_cols = _column_names(migrated_engine, table)
            orm_cols = _column_names(orm_engine, table)
            if mig_cols != orm_cols:
                mismatches.append(f"{table}: columns {mig_cols ^ orm_cols}")
            mig_fks = _fk_targets(migrated_engine, table)
            orm_fks = _fk_targets(orm_engine, table)
            if mig_fks != orm_fks:
                mismatches.append(f"{table}: fks differ")
        orm_engine.dispose()
        assert not mismatches, "\n".join(mismatches)


class TestMigrationChain:
    def test_single_head_revision(self):
        script = ScriptDirectory.from_config(_alembic_cfg("sqlite://"))
        assert script.get_heads() == [HEAD_REVISION]

    def test_downgrade_one_step_from_head(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'down.db'}"
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "-1")
        engine = create_engine(url)
        try:
            tables = _table_names(engine)
            assert "import_conflicts" in tables
            remark_cols = _column_names(engine, "remarks")
            assert "visibility" not in remark_cols
            assert "import_sync_state" in tables
            assert "parental_consent_records" in tables
            assert "action_tasks" in tables
            assert "intervention_events" in tables
            assert "user_mfa_recovery_codes" in tables
            revision = engine.connect().execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == "016_import_conflicts"
        finally:
            engine.dispose()
