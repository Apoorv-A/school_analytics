"""Detect and resolve ERP import conflicts with analytics-owned rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Attendance,
    AttendanceStatus,
    ImportConflict,
    ImportConflictEntityType,
    ImportConflictStatus,
    Remark,
    RemarkCategory,
    Score,
)

ANALYTICS_SOURCE_SYSTEM = "analytics"

SCORE_FIELDS = frozenset({"marks_obtained", "is_absent"})
REMARK_FIELDS = frozenset({"body", "category"})
ATTENDANCE_FIELDS = frozenset({"status"})


def is_analytics_owned_source(source_system: str | None) -> bool:
    return source_system == ANALYTICS_SOURCE_SYSTEM


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, RemarkCategory):
        return value.value
    if isinstance(value, AttendanceStatus):
        return value.value
    return value


def _diff_fields(
    local: dict[str, Any],
    incoming: dict[str, Any],
    *,
    allowed: frozenset[str],
) -> list[str]:
    changed: list[str] = []
    for field in allowed:
        local_val = _normalize_value(local.get(field))
        incoming_val = _normalize_value(incoming.get(field))
        if local_val != incoming_val:
            changed.append(field)
    return changed


def score_snapshots(
    existing: Score, incoming_marks, incoming_absent, incoming_source
) -> tuple[dict, dict]:
    local = {
        "marks_obtained": existing.marks_obtained,
        "is_absent": existing.is_absent,
        "source_system": existing.source_system,
    }
    incoming = {
        "marks_obtained": incoming_marks,
        "is_absent": incoming_absent,
        "source_system": incoming_source,
    }
    return local, incoming


def remark_snapshots(
    existing: Remark, incoming_body: str, incoming_category: str, incoming_source
) -> tuple[dict, dict]:
    local = {
        "body": existing.body,
        "category": existing.category.value,
        "source_system": existing.source_system,
    }
    incoming = {
        "body": incoming_body,
        "category": incoming_category,
        "source_system": incoming_source,
    }
    return local, incoming


def attendance_snapshots(
    existing: Attendance, incoming_status: str, incoming_source
) -> tuple[dict, dict]:
    local = {
        "status": existing.status.value,
        "source_system": existing.source_system,
    }
    incoming = {
        "status": incoming_status,
        "source_system": incoming_source,
    }
    return local, incoming


def queue_import_conflict(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    entity_type: ImportConflictEntityType,
    entity_id: int,
    external_id: str | None,
    source: str,
    local_values: dict[str, Any],
    incoming_values: dict[str, Any],
    field_names: list[str],
) -> ImportConflict:
    now = datetime.now(UTC)
    existing = db.scalars(
        select(ImportConflict).where(
            ImportConflict.tenant_id == tenant_id,
            ImportConflict.entity_type == entity_type,
            ImportConflict.entity_id == entity_id,
        )
    ).first()
    if existing is not None:
        if existing.status != ImportConflictStatus.PENDING:
            existing.status = ImportConflictStatus.PENDING
            existing.resolution = None
            existing.resolved_by_user_id = None
            existing.resolved_at = None
        existing.source = source
        existing.external_id = external_id
        existing.local_values = local_values
        existing.incoming_values = incoming_values
        existing.field_names = field_names
        existing.updated_at = now
        return existing

    row = ImportConflict(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        external_id=external_id,
        source=source,
        local_values=local_values,
        incoming_values=incoming_values,
        field_names=field_names,
        status=ImportConflictStatus.PENDING,
    )
    db.add(row)
    db.flush()
    return row


def list_import_conflicts(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    status: ImportConflictStatus | None = ImportConflictStatus.PENDING,
    limit: int = 100,
) -> list[ImportConflict]:
    query = select(ImportConflict).where(ImportConflict.tenant_id == tenant_id)
    if status is not None:
        query = query.where(ImportConflict.status == status)
    return list(
        db.scalars(
            query.order_by(ImportConflict.created_at.desc()).limit(limit)
        ).all()
    )


def _apply_score_values(score: Score, values: dict[str, Any]) -> None:
    if "marks_obtained" in values:
        score.marks_obtained = values["marks_obtained"]
    if "is_absent" in values:
        score.is_absent = bool(values["is_absent"])
    incoming_source = values.get("source_system")
    if incoming_source is not None:
        score.source_system = incoming_source


def _apply_remark_values(remark: Remark, values: dict[str, Any]) -> None:
    if "body" in values:
        remark.body = str(values["body"])
    if "category" in values:
        remark.category = RemarkCategory(str(values["category"]))
    incoming_source = values.get("source_system")
    if incoming_source is not None:
        remark.source_system = incoming_source


def _apply_attendance_values(attendance: Attendance, values: dict[str, Any]) -> None:
    if "status" in values:
        attendance.status = AttendanceStatus(str(values["status"]))
    incoming_source = values.get("source_system")
    if incoming_source is not None:
        attendance.source_system = incoming_source


def resolve_import_conflict(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    conflict_id: int,
    strategy: ImportConflictStatus,
    resolved_by_user_id: int,
    field_choices: dict[str, str] | None = None,
) -> ImportConflict:
    if strategy not in (
        ImportConflictStatus.KEEP_LOCAL,
        ImportConflictStatus.ACCEPT_ERP,
        ImportConflictStatus.PER_FIELD,
    ):
        raise ValueError("Invalid resolution strategy")

    row = db.scalars(
        select(ImportConflict).where(
            ImportConflict.id == conflict_id,
            ImportConflict.tenant_id == tenant_id,
        )
    ).first()
    if row is None:
        raise LookupError("Conflict not found")
    if row.status != ImportConflictStatus.PENDING:
        raise ValueError("Conflict is already resolved")

    now = datetime.now(UTC)
    local = dict(row.local_values)
    incoming = dict(row.incoming_values)

    if strategy is ImportConflictStatus.ACCEPT_ERP:
        merged = {**local, **incoming}
    elif strategy is ImportConflictStatus.KEEP_LOCAL:
        merged = None
    else:
        if not field_choices:
            raise ValueError("field_choices required for per-field resolution")
        allowed = set(row.field_names)
        merged = dict(local)
        for field, side in field_choices.items():
            if field not in allowed:
                raise ValueError(f"Field {field!r} is not part of this conflict")
            if side not in ("local", "incoming"):
                raise ValueError("field choice must be 'local' or 'incoming'")
            source_values = local if side == "local" else incoming
            if field not in source_values:
                raise ValueError(f"Missing value for field {field!r}")
            merged[field] = source_values[field]
        for field in allowed:
            if field not in field_choices:
                merged[field] = local[field]
        if incoming.get("source_system") is not None:
            merged["source_system"] = incoming["source_system"]

    if merged is not None:
        if row.entity_type is ImportConflictEntityType.SCORE:
            score = db.get(Score, row.entity_id)
            if score is None or score.tenant_id != tenant_id:
                raise LookupError("Score row not found")
            _apply_score_values(score, merged)
        elif row.entity_type is ImportConflictEntityType.REMARK:
            remark = db.get(Remark, row.entity_id)
            if remark is None or remark.tenant_id != tenant_id:
                raise LookupError("Remark row not found")
            _apply_remark_values(remark, merged)
        elif row.entity_type is ImportConflictEntityType.ATTENDANCE:
            attendance = db.get(Attendance, row.entity_id)
            if attendance is None or attendance.tenant_id != tenant_id:
                raise LookupError("Attendance row not found")
            _apply_attendance_values(attendance, merged)
        else:
            raise ValueError("Unsupported entity type")

    row.status = strategy
    row.resolution = field_choices if strategy is ImportConflictStatus.PER_FIELD else None
    row.resolved_by_user_id = resolved_by_user_id
    row.resolved_at = now
    row.updated_at = now
    return row
