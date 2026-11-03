"""Run fetch + validate/apply for one ERP connector."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.import_.apply import apply_directory
from app.import_.bundle_fingerprint import compute_bundle_fingerprint
from app.import_.connectors.base import ImportConnector
from app.import_.connectors.errors import ConnectorError
from app.import_.connectors.types import ConnectorKind, ConnectorSyncResult
from app.import_.history import record_import_run
from app.import_.sync_state import get_stored_fingerprint, save_applied_fingerprint
from app.import_.validate import validate_directory
from app.models import ImportRunStatus


def _source_label(kind: ConnectorKind) -> str:
    """Import run source values are capped at 40 characters in the DB."""
    return f"sync:{kind.value}"


def run_connector_sync(
    db: Session,
    tenant_id: uuid.UUID,
    connector: ImportConnector,
    *,
    force_apply: bool = False,
) -> ConnectorSyncResult:
    """Fetch a bundle from the connector and apply it for one tenant."""
    kind = connector.kind
    source = _source_label(kind)
    try:
        with connector.materialize_bundle() as directory:
            fingerprint = compute_bundle_fingerprint(directory)
            if not force_apply:
                stored = get_stored_fingerprint(db, tenant_id, source)
                if stored is not None and stored == fingerprint:
                    validation = validate_directory(directory)
                    report: dict[str, object] = {
                        "valid": True,
                        "applied": False,
                        "skipped": True,
                        "delta": {
                            "reason": "bundle_unchanged",
                            "fingerprint": fingerprint,
                        },
                        "errors": [],
                        "summary": validation.get("summary") or {},
                    }
                    record_import_run(
                        db,
                        tenant_id=tenant_id,
                        status=ImportRunStatus.SKIPPED,
                        source=source,
                        report=report,
                    )
                    db.commit()
                    return ConnectorSyncResult(
                        kind=kind,
                        source=source,
                        report=report,
                        applied=False,
                        skipped=True,
                    )

            report = apply_directory(db, tenant_id, directory, source=source)
            if report.get("applied"):
                save_applied_fingerprint(
                    db,
                    tenant_id=tenant_id,
                    source=source,
                    bundle_fingerprint=fingerprint,
                )
                db.commit()
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
