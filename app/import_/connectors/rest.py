"""REST polling ERP export connector (stub for Phase 2)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.import_.connectors.errors import ConnectorNotImplementedError
from app.import_.connectors.types import ConnectorKind


class RestPollingConnector:
    """Poll a REST export endpoint for CSV bundles (not yet implemented)."""

    kind = ConnectorKind.REST

    def __init__(self, *, base_url_secret_ref: str) -> None:
        self.base_url_secret_ref = base_url_secret_ref.strip()

    @contextmanager
    def materialize_bundle(self) -> Iterator[Path]:
        raise ConnectorNotImplementedError(
            "REST polling connector is planned for Phase 2; use webhook ingest "
            "(`POST /api/erp/import`) or a local drop directory until REST lands."
        )
        yield Path()  # pragma: no cover
