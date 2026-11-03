"""Run fetch + validate/apply for one ERP connector."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.import_.apply import apply_directory
from app.import_.connectors.base import ImportConnector
from app.import_.connectors.errors import ConnectorError
from app.import_.connectors.types import ConnectorKind, ConnectorSyncResult
from app.import_.history import record_import_run
from app.models import ImportRunStatus


def _source_label(kind: ConnectorKind) -> str:
    """Import run source values are capped at 40 characters in the DB."""
    return f"sync:{kind.value}"


def run_connector_sync(
    db: Session,
    tenant_id: uuid.UUID,
    connector: ImportConnector,
) -> ConnectorSyncResult:
    """Fetch a bundle from the connector and apply it for one tenant."""
    kind = connector.kind
    source = _source_label(kind)
    try:
        with connector.materialize_bundle() as directory:
            report = apply_directory(db, tenant_id, directory, source=source)
    except ConnectorError as exc:
        report = {
            "valid": False,
            "applied": False,
            "errors": [{"error": str(exc)}],
        }
        record_import_run(
            db,
            tenant_id=tenant_id,
            status=ImportRunStatus.FAILED,
            source=source,
            report=report,
        )
        db.commit()
        return ConnectorSyncResult(
            kind=kind, source=source, report=report, applied=False
        )

    applied = bool(report.get("applied"))
    return ConnectorSyncResult(
        kind=kind,
        source=source,
        report=report,
        applied=applied,
    )
