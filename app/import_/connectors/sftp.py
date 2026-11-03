"""SFTP ERP export connector — pull CSV bundles from a remote inbox."""

from __future__ import annotations

import io
import ipaddress
import logging
import re
import socket
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from app.import_.connectors.errors import ConnectorConfigError, ConnectorFetchError
from app.import_.connectors.types import ConnectorKind
from app.secrets import resolve_secret, validate_secret_ref

logger = logging.getLogger(__name__)

_HOSTNAME_PATTERN = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,252}[a-zA-Z0-9])?$"
)
_REMOTE_PATH_PATTERN = re.compile(r"^/[a-zA-Z0-9/_.-]+$")

SftpClientFactory = Callable[..., object]


def _validate_secret_ref_field(name: str, ref: str) -> str:
    cleaned = ref.strip()
    if not cleaned:
        raise ConnectorConfigError(f"sftp connector requires {name}")
    try:
        return validate_secret_ref(cleaned)
    except ValueError as exc:
        raise ConnectorConfigError(str(exc)) from exc


def _parse_host_port(raw: str) -> tuple[str, int]:
    value = raw.strip()
    if not value:
        raise ConnectorConfigError("SFTP host secret resolved to an empty value")
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            raise ConnectorConfigError("invalid SFTP host value (unclosed IPv6 bracket)")
        host = value[1:end].strip()
        remainder = value[end + 1 :].strip()
        if remainder == "":
            return host, 22
        if not remainder.startswith(":"):
            raise ConnectorConfigError("invalid SFTP host value after IPv6 address")
        port_str = remainder[1:].strip()
    elif value.count(":") == 1:
        host, port_str = value.rsplit(":", 1)
        host = host.strip()
        port_str = port_str.strip()
    else:
        return value, 22

    if not port_str.isdigit():
        raise ConnectorConfigError("SFTP port must be a number between 1 and 65535")
    port = int(port_str)
    if port < 1 or port > 65535:
        raise ConnectorConfigError("SFTP port must be a number between 1 and 65535")
    return host, port


def _validate_host(host: str) -> str:
    cleaned = host.strip()
    if not cleaned:
        raise ConnectorConfigError("SFTP host must not be empty")
    try:
        ipaddress.ip_address(cleaned)
        return cleaned
    except ValueError:
        pass
    if _HOSTNAME_PATTERN.match(cleaned):
        return cleaned
    raise ConnectorConfigError("SFTP host is not a valid hostname or IP address")


def _validate_remote_path(path: str) -> str:
    cleaned = path.strip()
    if not cleaned:
        raise ConnectorConfigError("SFTP remote path must not be empty")
    if not cleaned.startswith("/"):
        raise ConnectorConfigError("SFTP remote path must be absolute (start with /)")
    if not _REMOTE_PATH_PATTERN.match(cleaned):
        raise ConnectorConfigError(
            "SFTP remote path contains unsupported characters"
        )
    pure = PurePosixPath(cleaned)
    if any(part == ".." for part in pure.parts):
        raise ConnectorConfigError("SFTP remote path must not contain '..' segments")
    return str(pure)


def _load_private_key(pem: str, password: str | None) -> object:
    import paramiko

    key_password = password.encode("utf-8") if password else None
    buffer = io.StringIO(pem)
    for key_class in (
        paramiko.Ed25519Key,
        paramiko.RSAKey,
        paramiko.ECDSAKey,
    ):
        buffer.seek(0)
        try:
            return key_class.from_private_key(buffer, password=key_password)
        except paramiko.SSHException:
            continue
    raise ConnectorConfigError("unable to parse SFTP private key secret")


def _download_csv_files(
    sftp: object,
    remote_dir: str,
    dest_dir: Path,
) -> int:
    try:
        entries = sftp.listdir_attr(remote_dir)
    except OSError as exc:
        raise ConnectorFetchError(
            f"unable to list SFTP remote directory: {remote_dir}"
        ) from exc

    downloaded = 0
    for entry in entries:
        name = entry.filename
        if name in {".", ".."}:
            continue
        if not name.lower().endswith(".csv"):
            continue
        if not stat.S_ISREG(entry.st_mode):
            continue
        remote_file = f"{remote_dir.rstrip('/')}/{name}"
        local_path = dest_dir / name
        try:
            sftp.get(remote_file, str(local_path))
        except OSError as exc:
            raise ConnectorFetchError(
                f"unable to download SFTP file: {name}"
            ) from exc
        downloaded += 1
    return downloaded


def _open_sftp_session(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    private_key_pem: str,
    connect_timeout: float = 30.0,
    client_factory: SftpClientFactory | None = None,
) -> tuple[object, object]:
    import paramiko

    client = client_factory() if client_factory is not None else paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kwargs: dict[str, object] = {
        "hostname": host,
        "port": port,
        "username": username,
        "timeout": connect_timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if private_key_pem:
        connect_kwargs["pkey"] = _load_private_key(
            private_key_pem, password or None
        )
    elif password:
        connect_kwargs["password"] = password
    else:
        raise ConnectorConfigError(
            "sftp connector requires a password or private key secret ref"
        )

    try:
        client.connect(**connect_kwargs)
    except (TimeoutError, paramiko.SSHException, socket.gaierror, OSError) as exc:
        client.close()
        raise ConnectorFetchError("SFTP connection failed") from exc

    try:
        sftp = client.open_sftp()
    except paramiko.SSHException as exc:
        client.close()
        raise ConnectorFetchError("SFTP subsystem unavailable") from exc
    return client, sftp


class SftpConnector:
    """Pull CSV bundles from an SFTP inbox into a temp directory for apply."""

    kind = ConnectorKind.SFTP

    def __init__(
        self,
        *,
        host_secret_ref: str,
        path_secret_ref: str,
        user_secret_ref: str,
        password_secret_ref: str = "",
        private_key_secret_ref: str = "",
        client_factory: SftpClientFactory | None = None,
    ) -> None:
        self.host_secret_ref = _validate_secret_ref_field(
            "host secret ref", host_secret_ref
        )
        self.path_secret_ref = _validate_secret_ref_field(
            "path secret ref", path_secret_ref
        )
        self.user_secret_ref = _validate_secret_ref_field(
            "user secret ref", user_secret_ref
        )
        self.password_secret_ref = (
            _validate_secret_ref_field("password secret ref", password_secret_ref)
            if password_secret_ref.strip()
            else ""
        )
        self.private_key_secret_ref = (
            _validate_secret_ref_field("private key secret ref", private_key_secret_ref)
            if private_key_secret_ref.strip()
            else ""
        )
        if not self.password_secret_ref and not self.private_key_secret_ref:
            raise ConnectorConfigError(
                "sftp connector requires --sftp-password-secret-ref or "
                "--sftp-private-key-secret-ref"
            )
        self._client_factory = client_factory

    @contextmanager
    def materialize_bundle(self) -> Iterator[Path]:
        host_raw = resolve_secret(self.host_secret_ref, required=True)
        remote_path = _validate_remote_path(
            resolve_secret(self.path_secret_ref, required=True)
        )
        username = resolve_secret(self.user_secret_ref, required=True).strip()
        if not username:
            raise ConnectorConfigError("SFTP user secret resolved to an empty value")

        password = ""
        if self.password_secret_ref:
            password = resolve_secret(self.password_secret_ref, required=True)

        private_key_pem = ""
        if self.private_key_secret_ref:
            private_key_pem = resolve_secret(self.private_key_secret_ref, required=True)

        host, port = _parse_host_port(host_raw)
        host = _validate_host(host)

        client: object | None = None
        sftp: object | None = None
        with tempfile.TemporaryDirectory(prefix="school-sftp-import-") as tmp:
            dest = Path(tmp)
            try:
                client, sftp = _open_sftp_session(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    private_key_pem=private_key_pem,
                    client_factory=self._client_factory,
                )
                count = _download_csv_files(sftp, remote_path, dest)
            finally:
                if sftp is not None:
                    sftp.close()
                if client is not None:
                    client.close()

            if count == 0:
                raise ConnectorFetchError(
                    f"no CSV files found on SFTP at {remote_path}"
                )
            logger.info(
                "SFTP connector fetched %s CSV file(s) from %s:%s",
                count,
                host,
                port,
            )
            yield dest
