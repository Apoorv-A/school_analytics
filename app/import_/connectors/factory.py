"""Build connector instances from CLI / job configuration."""

from __future__ import annotations

from pathlib import Path

from app.import_.connectors.base import ImportConnector
from app.import_.connectors.errors import ConnectorConfigError
from app.import_.connectors.local import LocalPathConnector
from app.import_.connectors.rest import RestPollingConnector
from app.import_.connectors.sftp import SftpConnector
from app.import_.connectors.types import ConnectorKind


def build_connector(
    kind: ConnectorKind,
    *,
    local_dir: Path | None = None,
    sftp_host_secret_ref: str = "",
    sftp_path_secret_ref: str = "",
    rest_base_url_secret_ref: str = "",
) -> ImportConnector:
    if kind == ConnectorKind.LOCAL:
        if local_dir is None:
            raise ConnectorConfigError("local connector requires --dir")
        return LocalPathConnector(local_dir)
    if kind == ConnectorKind.SFTP:
        if not sftp_host_secret_ref or not sftp_path_secret_ref:
            raise ConnectorConfigError(
                "sftp connector requires host and path secret refs (stub only today)"
            )
        return SftpConnector(
            host_secret_ref=sftp_host_secret_ref,
            path_secret_ref=sftp_path_secret_ref,
        )
    if kind == ConnectorKind.REST:
        if not rest_base_url_secret_ref:
            raise ConnectorConfigError(
                "rest connector requires a base URL secret ref (stub only today)"
            )
        return RestPollingConnector(base_url_secret_ref=rest_base_url_secret_ref)
    raise ConnectorConfigError(f"unsupported connector kind: {kind}")
