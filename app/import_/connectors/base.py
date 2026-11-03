"""Connector protocol for ERP CSV bundle ingest."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from app.import_.connectors.types import ConnectorKind


class ImportConnector(Protocol):
    """Fetches a validated CSV bundle directory for ``apply_directory``."""

    kind: ConnectorKind

    @contextmanager
    def materialize_bundle(self) -> Iterator[Path]:
        """Yield a directory containing CSV files; cleanup temp data on exit when needed."""
        ...
