"""SFTP ERP export connector (stub for Phase 2)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.import_.connectors.errors import ConnectorNotImplementedError
from app.import_.connectors.types import ConnectorKind


class SftpConnector:
    """Pull CSV bundles from an SFTP inbox (not yet implemented)."""

    kind = ConnectorKind.SFTP

    def __init__(self, *, host_secret_ref: str, path_secret_ref: str) -> None:
        self.host_secret_ref = host_secret_ref.strip()
        self.path_secret_ref = path_secret_ref.strip()

    @contextmanager
    def materialize_bundle(self) -> Iterator[Path]:
        raise ConnectorNotImplementedError(
            "SFTP connector is planned for Phase 2; use a local drop directory "
            "and `python -m app.import_.connectors.cli sync --kind local` from a "
            "CronJob until SFTP lands."
        )
        yield Path()  # pragma: no cover
