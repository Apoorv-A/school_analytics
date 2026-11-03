"""Last-applied bundle fingerprint per tenant ERP sync channel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ImportSyncState


def get_stored_fingerprint(
    db: Session, tenant_id: uuid.UUID, source: str
) -> str | None:
    row = db.scalars(
        select(ImportSyncState).where(
            ImportSyncState.tenant_id == tenant_id,
            ImportSyncState.source == source,
        )
    ).first()
    if row is None:
        return None
    return row.bundle_fingerprint


def save_applied_fingerprint(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    source: str,
    bundle_fingerprint: str,
) -> ImportSyncState:
    row = db.scalars(
        select(ImportSyncState).where(
            ImportSyncState.tenant_id == tenant_id,
            ImportSyncState.source == source,
        )
    ).first()
    now = datetime.now(UTC)
    if row is None:
        row = ImportSyncState(
            tenant_id=tenant_id,
            source=source,
            bundle_fingerprint=bundle_fingerprint,
            updated_at=now,
        )
        db.add(row)
    else:
        row.bundle_fingerprint = bundle_fingerprint
        row.updated_at = now
    db.flush()
    return row
