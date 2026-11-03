"""ERP connector framework and scheduled sync CLI."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from sqlalchemy import select

import tests._ensure_paramiko  # noqa: F401 — stub paramiko when not installed locally
from app.import_.connectors.errors import (
    ConnectorConfigError,
    ConnectorFetchError,
    ConnectorNotImplementedError,
)
from app.import_.connectors.factory import build_connector
from app.import_.connectors.local import LocalPathConnector
from app.import_.connectors.runner import run_connector_sync
from app.import_.connectors.sftp import (
    SftpConnector,
    _parse_host_port,
    _validate_remote_path,
)
from app.import_.connectors.types import ConnectorKind
from app.models import ImportRun, Tenant
from app.secrets import secret_env_name


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


class TestSftpConnectorConfig:
    def test_requires_user_and_credential_refs(self) -> None:
        with pytest.raises(ConnectorConfigError):
            build_connector(
                ConnectorKind.SFTP,
                sftp_host_secret_ref="erp/sftp/host",
                sftp_path_secret_ref="erp/sftp/path",
            )
        with pytest.raises(ConnectorConfigError):
            build_connector(
                ConnectorKind.SFTP,
                sftp_host_secret_ref="erp/sftp/host",
                sftp_path_secret_ref="erp/sftp/path",
                sftp_user_secret_ref="erp/sftp/user",
            )

    def test_rejects_unsafe_remote_path(self) -> None:
        with pytest.raises(ConnectorConfigError):
            _validate_remote_path("../etc/passwd")
        with pytest.raises(ConnectorConfigError):
            _validate_remote_path("relative/inbox")

    def test_parse_host_port(self) -> None:
        assert _parse_host_port("sftp.example.com") == ("sftp.example.com", 22)
        assert _parse_host_port("sftp.example.com:2222") == ("sftp.example.com", 2222)


class _FakeSftpAttr:
    def __init__(self, filename: str, *, is_file: bool = True) -> None:
        self.filename = filename
        self.st_mode = stat.S_IFREG if is_file else stat.S_IFDIR


class _FakeSftp:
    def __init__(self, remote_files: dict[str, str]) -> None:
        self._remote_files = remote_files

    def listdir_attr(self, remote_dir: str) -> list[_FakeSftpAttr]:
        prefix = remote_dir.rstrip("/") + "/"
        names: list[str] = []
        for remote_path in self._remote_files:
            if remote_path.startswith(prefix):
                remainder = remote_path[len(prefix) :]
                if "/" not in remainder:
                    names.append(remainder)
        return [_FakeSftpAttr(name) for name in sorted(names)]

    def get(self, remote_path: str, local_path: str) -> None:
        Path(local_path).write_text(
            self._remote_files[remote_path], encoding="utf-8"
        )

    def close(self) -> None:
        return None


class _FakeSshClient:
    def __init__(self, sftp: _FakeSftp) -> None:
        self._sftp = sftp

    def set_missing_host_key_policy(self, _policy: object) -> None:
        return None

    def connect(self, **_kwargs: object) -> None:
        return None

    def open_sftp(self) -> _FakeSftp:
        return self._sftp

    def close(self) -> None:
        return None


class TestSftpConnectorFetch:
    def test_downloads_csv_to_temp_and_applies(
        self, db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        host_ref = "erp/sftp/host"
        path_ref = "erp/sftp/path"
        user_ref = "erp/sftp/user"
        password_ref = "erp/sftp/password"
        monkeypatch.setenv(secret_env_name(host_ref), "sftp.example.com:22")
        monkeypatch.setenv(secret_env_name(path_ref), "/exports/sunrise")
        monkeypatch.setenv(secret_env_name(user_ref), "erp-sync")
        monkeypatch.setenv(secret_env_name(password_ref), "test-pass")

        csv_body = (
            "grade_level,grade_name,section_name,subject_code,subject_name\n"
            "8,SFTP Grade,SFTPSEC,SFTPTEST,SFTP Test Subject\n"
        )
        fake_sftp = _FakeSftp(
            {"/exports/sunrise/school_structure.csv": csv_body},
        )

        def fake_factory() -> _FakeSshClient:
            return _FakeSshClient(fake_sftp)

        connector = SftpConnector(
            host_secret_ref=host_ref,
            path_secret_ref=path_ref,
            user_secret_ref=user_ref,
            password_secret_ref=password_ref,
            client_factory=fake_factory,
        )
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        result = run_connector_sync(db, tenant.id, connector)
        assert result.source == "sync:sftp"
        assert result.applied is True

    def test_no_csv_files_raises_fetch_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        host_ref = "erp/sftp/host"
        path_ref = "erp/sftp/path"
        user_ref = "erp/sftp/user"
        password_ref = "erp/sftp/password"
        monkeypatch.setenv(secret_env_name(host_ref), "127.0.0.1")
        monkeypatch.setenv(secret_env_name(path_ref), "/exports/empty")
        monkeypatch.setenv(secret_env_name(user_ref), "erp-sync")
        monkeypatch.setenv(secret_env_name(password_ref), "test-pass")

        fake_sftp = _FakeSftp({"/exports/empty/readme.txt": "no csv here"})

        connector = SftpConnector(
            host_secret_ref=host_ref,
            path_secret_ref=path_ref,
            user_secret_ref=user_ref,
            password_secret_ref=password_ref,
            client_factory=lambda: _FakeSshClient(fake_sftp),
        )
        with pytest.raises(ConnectorFetchError):
            with connector.materialize_bundle():
                pass


class TestConnectorStubs:
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
