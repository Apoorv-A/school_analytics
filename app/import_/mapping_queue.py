"""Queue and resolve unmapped external assessment keys during import."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assessment,
    ImportMappingQueue,
    ImportMappingQueueStatus,
)


def score_payload_snippet(
    *,
    admission_no: str,
    marks_obtained: float | None,
    is_absent: bool,
    source_system: str | None,
    external_id: str | None,
) -> dict[str, Any]:
    """Build a safe, minimal payload for admin mapping review."""
    return {
        "admission_no": admission_no,
        "marks_obtained": marks_obtained,
        "is_absent": is_absent,
        "source_system": source_system,
        "external_id": external_id,
    }


def queue_unmapped_assessment(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    source: str,
    external_key: str,
    payload_snippet: dict[str, Any],
) -> ImportMappingQueue | None:
    """Insert or refresh a pending queue row for an unknown assessment key.

    Returns ``None`` when the key was previously rejected by an administrator.
    """
    normalized_key = external_key.strip()
    if not normalized_key:
        return None

    existing = db.scalars(
        select(ImportMappingQueue).where(
            ImportMappingQueue.tenant_id == tenant_id,
            ImportMappingQueue.external_key == normalized_key,
        )
    ).first()
    if existing is not None:
        if existing.status == ImportMappingQueueStatus.REJECTED:
            return None
        if existing.status == ImportMappingQueueStatus.MAPPED:
            return existing
        existing.source = source
        existing.payload_snippet = payload_snippet
        existing.updated_at = datetime.now(UTC)
        db.flush()
        return existing

    row = ImportMappingQueue(
        tenant_id=tenant_id,
        source=source,
        external_key=normalized_key,
        payload_snippet=payload_snippet,
        status=ImportMappingQueueStatus.PENDING,
    )
    db.add(row)
    db.flush()
    return row


def resolve_mapping_to_assessment(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    queue_id: int,
    assessment_id: int,
    resolved_by_user_id: int,
) -> tuple[ImportMappingQueue, Assessment]:
    """Map a queued external key to an existing assessment."""
    queue_row = db.scalars(
        select(ImportMappingQueue).where(
            ImportMappingQueue.id == queue_id,
            ImportMappingQueue.tenant_id == tenant_id,
        )
    ).first()
    if queue_row is None:
        raise LookupError("mapping queue item not found")
    if queue_row.status != ImportMappingQueueStatus.PENDING:
        raise ValueError("mapping queue item is not pending")

    assessment = db.scalars(
        select(Assessment).where(
            Assessment.id == assessment_id,
            Assessment.tenant_id == tenant_id,
        )
    ).first()
    if assessment is None:
        raise LookupError("assessment not found")

    conflict = db.scalars(
        select(Assessment).where(
            Assessment.tenant_id == tenant_id,
            Assessment.external_id == queue_row.external_key,
            Assessment.id != assessment.id,
        )
    ).first()
    if conflict is not None:
        raise ValueError("external key is already mapped to another assessment")

    if assessment.external_id and assessment.external_id != queue_row.external_key:
        raise ValueError("assessment already has a different external key")

    assessment.external_id = queue_row.external_key
    now = datetime.now(UTC)
    queue_row.status = ImportMappingQueueStatus.MAPPED
    queue_row.mapped_assessment_id = assessment.id
    queue_row.resolved_by_user_id = resolved_by_user_id
    queue_row.resolved_at = now
    queue_row.updated_at = now
    db.flush()
    return queue_row, assessment


def reject_mapping(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    queue_id: int,
    resolved_by_user_id: int,
) -> ImportMappingQueue:
    """Reject a queued external assessment key."""
    queue_row = db.scalars(
        select(ImportMappingQueue).where(
            ImportMappingQueue.id == queue_id,
            ImportMappingQueue.tenant_id == tenant_id,
        )
    ).first()
    if queue_row is None:
        raise LookupError("mapping queue item not found")
    if queue_row.status != ImportMappingQueueStatus.PENDING:
        raise ValueError("mapping queue item is not pending")

    now = datetime.now(UTC)
    queue_row.status = ImportMappingQueueStatus.REJECTED
    queue_row.resolved_by_user_id = resolved_by_user_id
    queue_row.resolved_at = now
    queue_row.updated_at = now
    db.flush()
    return queue_row


def list_mapping_queue(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    status: ImportMappingQueueStatus | None = ImportMappingQueueStatus.PENDING,
    limit: int = 100,
) -> list[ImportMappingQueue]:
    query = select(ImportMappingQueue).where(ImportMappingQueue.tenant_id == tenant_id)
    if status is not None:
        query = query.where(ImportMappingQueue.status == status)
    return list(
        db.scalars(
            query.order_by(ImportMappingQueue.created_at.desc()).limit(limit)
        )
    )
