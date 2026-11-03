"""ERP import connectors (Phase 2 scheduled sync foundation)."""

from app.import_.connectors.runner import run_connector_sync
from app.import_.connectors.types import ConnectorKind, ConnectorSyncResult

__all__ = [
    "ConnectorKind",
    "ConnectorSyncResult",
    "run_connector_sync",
]
