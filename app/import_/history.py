"""Import run audit log (Phase 2 ERP sync foundation)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ImportRun, ImportRunStatus


def record_import_run(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    status: ImportRunStatus,
    source: str,
    report: dict[str, Any],
    user_id: int | None = None,
) -> ImportRun:
    summary = report.get("summary") or report.get("rows") or {}
    file_names = sorted(summary.keys()) if isinstance(summary, dict) else []
    errors = report.get("errors") or []
    run = ImportRun(
        tenant_id=tenant_id,
        status=status,
        source=source,
        file_summary=", ".join(file_names) if file_names else None,
        row_counts=summary if isinstance(summary, dict) else None,
        error_count=len(errors),
        created_by_user_id=user_id,
    )
    db.add(run)
    db.flush()
    return run


def recent_import_runs(
    db: Session, tenant_id: uuid.UUID, *, limit: int = 10
) -> list[ImportRun]:
    return list(
        db.scalars(
            select(ImportRun)
            .where(ImportRun.tenant_id == tenant_id)
            .order_by(ImportRun.created_at.desc())
            .limit(limit)
        )
    )
