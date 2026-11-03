"""REST polling ERP export connector — fetch CSV bundles over HTTPS."""

from __future__ import annotations

import errno
import io
import ipaddress
import logging
import re
import socket
import ssl
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.import_.connectors.errors import ConnectorConfigError, ConnectorFetchError
from app.import_.connectors.types import ConnectorKind
from app.import_.files import FILE_MODELS
from app.import_.limits import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_TOTAL_BYTES,
    MAX_ZIP_DECOMPRESSED_BYTES,
)
from app.secrets import resolve_secret, validate_secret_ref

logger = logging.getLogger(__name__)

_ZIP_MAGIC = b"PK\x03\x04"
_CONTENT_DISPOSITION_FILENAME = re.compile(
    r"""filename\*=UTF-8''([^;\s]+)|filename="([^"]+)"|filename=([^;\s]+)""",
    re.IGNORECASE,
)

HttpClientFactory = Callable[[], httpx.Client]


def _validate_secret_ref_field(name: str, ref: str) -> str:
    cleaned = ref.strip()
    if not cleaned:
        raise ConnectorConfigError(f"rest connector requires {name}")
    try:
        return validate_secret_ref(cleaned)
    except ValueError as exc:
        raise ConnectorConfigError(str(exc)) from exc


def _host_is_blocked(host: str) -> bool:
    lowered = host.strip().lower().rstrip(".")
    if lowered in {"localhost"}:
        return True
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def _validate_export_url(raw: str, *, allow_http: bool) -> str:
    value = raw.strip()
    if not value:
        raise ConnectorConfigError("REST base URL secret resolved to an empty value")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ConnectorConfigError("REST export URL must use http or https")
    if not allow_http and parsed.scheme != "https":
        raise ConnectorConfigError(
            "REST export URL must use https when DEBUG is false (production)"
        )
    if parsed.username or parsed.password:
        raise ConnectorConfigError(
            "REST export URL must not embed credentials; use secret refs for auth"
        )
    if not parsed.netloc:
        raise ConnectorConfigError("REST export URL must include a host")
    host = parsed.hostname or ""
    if not host:
        raise ConnectorConfigError("REST export URL must include a host")
    if _host_is_blocked(host):
        raise ConnectorConfigError("REST export URL host is not allowed")
    return value


def _parse_content_disposition_filename(header: str | None) -> str | None:
    if not header:
        return None
    match = _CONTENT_DISPOSITION_FILENAME.search(header)
    if not match:
        return None
    for group in match.groups():
        if group:
            return Path(group).name
    return None


def _copy_limited_bytes(data: bytes, dest: Path, *, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise ConnectorFetchError(
            f"REST export exceeds {max_bytes // (1024 * 1024)} MB limit"
        )
    dest.write_bytes(data)


def _extract_zip_bytes(payload: bytes, dest_dir: Path) -> int:
    allowed = set(FILE_MODELS)
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ConnectorFetchError("REST export is not a valid zip archive") from exc

    extracted = 0
    decompressed = 0
    with archive:
        for member in archive.namelist():
            name = Path(member).name
            if name not in allowed:
                continue
            info = archive.getinfo(member)
            if info.file_size > MAX_UPLOAD_BYTES:
                raise ConnectorFetchError(
                    f"{name}: member exceeds per-file size limit"
                )
            decompressed += info.file_size
            if decompressed > MAX_ZIP_DECOMPRESSED_BYTES:
                raise ConnectorFetchError(
                    "REST zip export exceeds decompressed size limit "
                    f"({MAX_ZIP_DECOMPRESSED_BYTES // (1024 * 1024)} MB)"
                )
            target = dest_dir / name
            with archive.open(member) as src:
                body = src.read()
            if len(body) > MAX_UPLOAD_BYTES:
                raise ConnectorFetchError(
                    f"{name}: member exceeds per-file size limit"
                )
            target.write_bytes(body)
            extracted += 1
    return extracted


def _materialize_response_body(body: bytes, dest_dir: Path, *, filename: str | None) -> int:
    if not body:
        raise ConnectorFetchError("REST export returned an empty response")

    if body.startswith(_ZIP_MAGIC):
        count = _extract_zip_bytes(body, dest_dir)
        if count == 0:
            raise ConnectorFetchError("No recognized CSV files in REST zip export")
        return count

    name = Path(filename or "").name
    if name not in FILE_MODELS:
        raise ConnectorFetchError(
            "REST export must be a zip of recognized CSV files or a single "
            f"allowed CSV (got {name or 'unknown filename'})"
        )
    _copy_limited_bytes(body, dest_dir / name, max_bytes=MAX_UPLOAD_BYTES)
    return 1


def _export_host_label(export_url: str) -> str:
    parsed = urlparse(export_url)
    if parsed.hostname:
        return parsed.hostname
    return parsed.netloc or "unknown host"


def _describe_rest_http_error(exc: httpx.HTTPError, export_url: str) -> str:
    """Build a user-safe message from an httpx failure (no URLs with secrets)."""
    host = _export_host_label(export_url)
    if isinstance(exc, httpx.ConnectTimeout):
        return f"REST export connect timed out for {host}"
    if isinstance(exc, httpx.ReadTimeout):
        return f"REST export read timed out for {host}"
    if isinstance(exc, httpx.TimeoutException):
        return f"REST export request timed out for {host}"
    if isinstance(exc, httpx.UnsupportedProtocol):
        return "REST export URL scheme is not supported (use http or https)"

    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return f"REST export host could not be resolved (DNS): {host}"
    if isinstance(cause, ssl.SSLCertVerificationError):
        return f"REST export TLS certificate verification failed for {host}"
    if isinstance(cause, ssl.SSLError):
        return f"REST export TLS handshake failed for {host}"
    if isinstance(cause, ConnectionRefusedError):
        return f"REST export connection refused by {host}"
    if isinstance(cause, OSError):
        if cause.errno == errno.ECONNREFUSED:
            return f"REST export connection refused by {host}"
        if cause.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            return f"REST export host unreachable: {host}"

    if isinstance(exc, httpx.ConnectError):
        return f"REST export could not connect to {host}"
    if isinstance(exc, httpx.NetworkError):
        return f"REST export network error for {host}"
    return f"REST export request failed for {host}"


def _read_response_bytes(response: httpx.Response) -> bytes:
    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_UPLOAD_TOTAL_BYTES:
            raise ConnectorFetchError(
                "REST export exceeds "
                f"{MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)} MB response limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_export(
    client: httpx.Client,
    url: str,
    *,
    bearer_token: str,
    basic_user: str,
    basic_password: str,
) -> tuple[bytes, str | None]:
    headers: dict[str, str] = {}
    auth: tuple[str, str] | None = None
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif basic_user:
        auth = (basic_user, basic_password)

    try:
        response = client.get(url, headers=headers, auth=auth)
    except httpx.HTTPError as exc:
        host = _export_host_label(url)
        logger.warning(
            "REST export HTTP error for %s (%s)",
            host,
            type(exc).__name__,
        )
        raise ConnectorFetchError(_describe_rest_http_error(exc, url)) from exc

    if response.status_code >= 400:
        host = _export_host_label(url)
        raise ConnectorFetchError(
            f"REST export request failed with HTTP {response.status_code} from {host}"
        )

    filename = _parse_content_disposition_filename(
        response.headers.get("content-disposition")
    )
    body = _read_response_bytes(response)
    return body, filename


class RestPollingConnector:
    """Poll a REST export endpoint for CSV bundles (zip or single CSV)."""

    kind = ConnectorKind.REST

    def __init__(
        self,
        *,
        base_url_secret_ref: str,
        bearer_token_secret_ref: str = "",
        basic_user_secret_ref: str = "",
        basic_password_secret_ref: str = "",
        client_factory: HttpClientFactory | None = None,
    ) -> None:
        self.base_url_secret_ref = _validate_secret_ref_field(
            "base URL secret ref", base_url_secret_ref
        )
        self.bearer_token_secret_ref = (
            _validate_secret_ref_field("bearer token secret ref", bearer_token_secret_ref)
            if bearer_token_secret_ref.strip()
            else ""
        )
        self.basic_user_secret_ref = (
            _validate_secret_ref_field("basic user secret ref", basic_user_secret_ref)
            if basic_user_secret_ref.strip()
            else ""
        )
        self.basic_password_secret_ref = (
            _validate_secret_ref_field(
                "basic password secret ref", basic_password_secret_ref
            )
            if basic_password_secret_ref.strip()
            else ""
        )
        if self.bearer_token_secret_ref and (
            self.basic_user_secret_ref or self.basic_password_secret_ref
        ):
            raise ConnectorConfigError(
                "rest connector accepts either --rest-bearer-token-secret-ref or "
                "basic auth secret refs, not both"
            )
        if self.basic_user_secret_ref or self.basic_password_secret_ref:
            if not self.basic_user_secret_ref or not self.basic_password_secret_ref:
                raise ConnectorConfigError(
                    "rest basic auth requires both --rest-basic-user-secret-ref and "
                    "--rest-basic-password-secret-ref"
                )
        if (
            not self.bearer_token_secret_ref
            and not self.basic_user_secret_ref
        ):
            raise ConnectorConfigError(
                "rest connector requires --rest-bearer-token-secret-ref or "
                "basic auth secret refs"
            )
        self._client_factory = client_factory

    def _open_client(self) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(timeout=120.0, follow_redirects=False)

    @contextmanager
    def materialize_bundle(self) -> Iterator[Path]:
        allow_http = get_settings().debug
        export_url = _validate_export_url(
            resolve_secret(self.base_url_secret_ref, required=True),
            allow_http=allow_http,
        )

        bearer_token = ""
        if self.bearer_token_secret_ref:
            bearer_token = resolve_secret(self.bearer_token_secret_ref, required=True)

        basic_user = ""
        basic_password = ""
        if self.basic_user_secret_ref:
            basic_user = resolve_secret(self.basic_user_secret_ref, required=True).strip()
            basic_password = resolve_secret(
                self.basic_password_secret_ref, required=True
            )
            if not basic_user:
                raise ConnectorConfigError(
                    "REST basic user secret resolved to an empty value"
                )

        client = self._open_client()
        owns_client = self._client_factory is None
        with tempfile.TemporaryDirectory(prefix="school-rest-import-") as tmp:
            dest = Path(tmp)
            try:
                body, filename = _fetch_export(
                    client,
                    export_url,
                    bearer_token=bearer_token,
                    basic_user=basic_user,
                    basic_password=basic_password,
                )
                count = _materialize_response_body(body, dest, filename=filename)
            finally:
                if owns_client:
                    client.close()

            logger.info(
                "REST connector fetched %s CSV file(s) from %s",
                count,
                urlparse(export_url).netloc,
            )
            yield dest
