"""ERP connector framework and scheduled sync CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.import_.connectors.errors import (
    ConnectorConfigError,
    ConnectorNotImplementedError,
)
from app.import_.connectors.factory import build_connector
from app.import_.connectors.local import LocalPathConnector
from app.import_.connectors.runner import run_connector_sync
from app.import_.connectors.types import ConnectorKind
from app.models import ImportRun, Tenant


def _write_csv(path: Path, header: str, row: str) -> None:
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")


@pytest.fixture
def connector_bundle_dir(tmp_path: Path) -> Path:
    _write_csv(
        tmp_path / "school_structure.csv",
        "grade_level,grade_name,section_name,subject_code,subject_name",
        "8,Connector Test Grade,CONN,CONNTEST,Connector Test Subject",
    )
    return tmp_path


class TestLocalConnector:
    def test_requires_absolute_path(self, tmp_path: Path) -> None:
        with pytest.raises(ConnectorConfigError):
            LocalPathConnector(Path("relative/path"))

    def test_missing_directory_raises_on_fetch(self, tmp_path: Path) -> None:
        missing = tmp_path / "drop"
        connector = LocalPathConnector(missing)
        with pytest.raises(Exception) as exc_info:
            with connector.materialize_bundle():
                pass
        assert "not found" in str(exc_info.value).lower()


class TestConnectorStubs:
    def test_sftp_not_implemented(self) -> None:
        connector = build_connector(
            ConnectorKind.SFTP,
            sftp_host_secret_ref="erp/sftp/host",
            sftp_path_secret_ref="erp/sftp/path",
        )
        with pytest.raises(ConnectorNotImplementedError):
            with connector.materialize_bundle():
                pass

    def test_rest_not_implemented(self) -> None:
        connector = build_connector(
            ConnectorKind.REST,
            rest_base_url_secret_ref="erp/rest/base",
        )
        with pytest.raises(ConnectorNotImplementedError):
            with connector.materialize_bundle():
                pass


class TestRunConnectorSync:
    def test_local_sync_records_import_run(
        self, db, connector_bundle_dir: Path
    ) -> None:
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        connector = LocalPathConnector(connector_bundle_dir)
        result = run_connector_sync(db, tenant.id, connector)
        assert result.source == "sync:local"
        assert result.applied is True

        run = db.scalars(
            select(ImportRun)
            .where(
                ImportRun.tenant_id == tenant.id,
                ImportRun.source == "sync:local",
            )
            .order_by(ImportRun.created_at.desc())
        ).first()
        assert run is not None
        assert run.status.value == "applied"

    def test_fetch_failure_records_failed_run(self, db, tmp_path: Path) -> None:
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        connector = LocalPathConnector(tmp_path / "missing")
        result = run_connector_sync(db, tenant.id, connector)
        assert result.applied is False
        assert result.report["errors"]
