"""Local filesystem drop directory connector."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.import_.connectors.errors import ConnectorConfigError, ConnectorFetchError
from app.import_.connectors.types import ConnectorKind


class LocalPathConnector:
    """Apply CSV bundles from a mounted directory (CronJob / SFTP sidecar drop)."""

    kind = ConnectorKind.LOCAL

    def __init__(self, directory: Path) -> None:
        expanded = directory.expanduser()
        if not expanded.is_absolute():
            raise ConnectorConfigError("local connector path must be absolute")
        self._directory = expanded.resolve()

    @contextmanager
    def materialize_bundle(self) -> Iterator[Path]:
        if not self._directory.is_dir():
            raise ConnectorFetchError(f"local drop directory not found: {self._directory}")
        yield self._directory
