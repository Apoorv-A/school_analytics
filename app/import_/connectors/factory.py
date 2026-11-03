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
    sftp_user_secret_ref: str = "",
    sftp_password_secret_ref: str = "",
    sftp_private_key_secret_ref: str = "",
    rest_base_url_secret_ref: str = "",
    rest_bearer_token_secret_ref: str = "",
    rest_basic_user_secret_ref: str = "",
    rest_basic_password_secret_ref: str = "",
) -> ImportConnector:
    if kind == ConnectorKind.LOCAL:
        if local_dir is None:
            raise ConnectorConfigError("local connector requires --dir")
        return LocalPathConnector(local_dir)
    if kind == ConnectorKind.SFTP:
        if not sftp_host_secret_ref or not sftp_path_secret_ref:
            raise ConnectorConfigError(
                "sftp connector requires --sftp-host-secret-ref and "
                "--sftp-path-secret-ref"
            )
        if not sftp_user_secret_ref:
            raise ConnectorConfigError("sftp connector requires --sftp-user-secret-ref")
        return SftpConnector(
            host_secret_ref=sftp_host_secret_ref,
            path_secret_ref=sftp_path_secret_ref,
            user_secret_ref=sftp_user_secret_ref,
            password_secret_ref=sftp_password_secret_ref,
            private_key_secret_ref=sftp_private_key_secret_ref,
        )
    if kind == ConnectorKind.REST:
        if not rest_base_url_secret_ref:
            raise ConnectorConfigError(
                "rest connector requires --rest-base-url-secret-ref"
            )
        return RestPollingConnector(
            base_url_secret_ref=rest_base_url_secret_ref,
            bearer_token_secret_ref=rest_bearer_token_secret_ref,
            basic_user_secret_ref=rest_basic_user_secret_ref,
            basic_password_secret_ref=rest_basic_password_secret_ref,
        )
    raise ConnectorConfigError(f"unsupported connector kind: {kind}")
