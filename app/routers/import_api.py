"""Admin CSV import API (behind bulkImport feature flag)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.import_.apply import apply_directory
from app.import_.limits import MAX_UPLOAD_BYTES
from app.import_.upload_io import extract_archive_upload
from app.import_.validate import validate_directory
from app.models import Role
from app.tenant.context import require_tenant_context
from app.tenant.features import require_bulk_import_enabled

router = APIRouter(prefix="/api/import", tags=["import"])


def _failed_upload_report(extract_errors: list[str], filename: str) -> dict[str, object]:
    return {
        "valid": False,
        "applied": False,
        "errors": [
            {"file": filename, "row": "0", "error": err} for err in extract_errors
        ],
    }


@router.post("/validate")
async def validate_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
    _enabled: None = Depends(require_bulk_import_enabled),
) -> JSONResponse:
    tenant_ctx = require_tenant_context()
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"valid": False, "errors": [{"error": "Upload exceeds 5 MB limit."}]},
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    await file.seek(0)

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        extract_errors = extract_archive_upload(file, dest)
        if extract_errors:
            report = _failed_upload_report(extract_errors, file.filename or "")
            from app.import_.history import record_import_run
            from app.models import ImportRunStatus

            record_import_run(
                db,
                tenant_id=tenant_ctx.tenant_id,
                status=ImportRunStatus.FAILED,
                source="upload",
                report=report,
                user_id=scope.user.id,
            )
            db.commit()
            return JSONResponse(report)

        report = validate_directory(dest)
        from app.import_.history import record_import_run
        from app.models import ImportRunStatus

        record_import_run(
            db,
            tenant_id=tenant_ctx.tenant_id,
            status=ImportRunStatus.VALIDATED if report["valid"] else ImportRunStatus.FAILED,
            source="upload",
            report=report,
            user_id=scope.user.id,
        )
        db.commit()
        return JSONResponse(report)


@router.post("/apply")
async def apply_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
    _enabled: None = Depends(require_bulk_import_enabled),
) -> JSONResponse:
    tenant_ctx = require_tenant_context()
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"applied": False, "errors": [{"error": "Upload exceeds 5 MB limit."}]},
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    await file.seek(0)

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        extract_errors = extract_archive_upload(file, dest)
        if extract_errors:
            report = _failed_upload_report(extract_errors, file.filename or "")
            from app.import_.history import record_import_run
            from app.models import ImportRunStatus

            record_import_run(
                db,
                tenant_id=tenant_ctx.tenant_id,
                status=ImportRunStatus.FAILED,
                source="upload",
                report=report,
                user_id=scope.user.id,
            )
            db.commit()
            return JSONResponse(report)

        report = apply_directory(
            db,
            tenant_ctx.tenant_id,
            dest,
            source="upload",
            user_id=scope.user.id,
        )
        return JSONResponse(report)
