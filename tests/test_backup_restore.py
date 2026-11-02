"""Backup and post-restore validation (SQLite, no Docker)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from scripts.backup_tenant_data import export_tenant_json, run_backup
from scripts.validate_restore import run_validation
from seed.generate import DEMO_PASSWORD, SUNRISE_HOST

pytestmark = pytest.mark.usefixtures("seeded_database")


class TestBackupTenantData:
    def test_full_sqlite_backup_creates_copy(self, tmp_path: Path):
        from app.config import settings

        dest = tmp_path / "school-copy.db"
        summary = run_backup(
            output=dest,
            tenant_key=None,
            database_url=settings.database_url,
        )
        assert summary["mode"] == "full"
        assert dest.is_file()
        assert dest.stat().st_size > 0

    def test_tenant_json_export_contains_core_tables(self, tmp_path: Path):
        from app.config import settings

        dest = tmp_path / "sunrise.json"
        engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
        )
        counts = export_tenant_json(engine, "sunrise", dest)
        engine.dispose()

        assert counts["tenants"] == 1
        assert counts["tenant_domains"] >= 1
        assert counts["users"] > 0
        payload = json.loads(dest.read_text(encoding="utf-8"))
        assert payload["tenant_key"] == "sunrise"
        assert "schools" in payload["tables"]


class TestValidateRestore:
    def test_passes_on_seeded_database(self):
        results = run_validation(
            database_url=os.environ["DATABASE_URL"],
            tenant_key="sunrise",
            hostname=SUNRISE_HOST,
            sample_password=DEMO_PASSWORD,
        )
        assert all(r.ok for r in results), [r for r in results if not r.ok]

    def test_passes_on_restored_sqlite_copy(self, tmp_path: Path):
        from app.config import settings

        source = Path(settings.database_url.replace("sqlite:///", ""))
        restored = tmp_path / "restored.db"
        shutil.copy2(source, restored)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(source) + suffix)
            if sidecar.is_file():
                shutil.copy2(sidecar, Path(str(restored) + suffix))

        restored_url = f"sqlite:///{restored}"
        results = run_validation(
            database_url=restored_url,
            tenant_key="sunrise",
            hostname=SUNRISE_HOST,
            sample_password=DEMO_PASSWORD,
        )
        names = {r.name: r for r in results}
        assert names["alembic_head"].ok
        assert names["tenant_domains"].ok
        assert names["sample_login"].ok
        assert names["rls_enabled"].ok

    def test_fails_when_hostname_unknown(self):
        results = run_validation(
            database_url=os.environ["DATABASE_URL"],
            tenant_key="sunrise",
            hostname="nonexistent.example.test",
            sample_password=DEMO_PASSWORD,
        )
        domain_check = next(r for r in results if r.name == "tenant_domains")
        assert domain_check.ok is False
