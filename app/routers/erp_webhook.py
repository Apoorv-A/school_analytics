"""ERP webhook ingest (Phase 2 connector entry point)."""

from __future__ import annotations

import hmac
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.import_.apply import apply_directory
from app.import_.limits import MAX_UPLOAD_BYTES
from app.import_.upload_io import extract_archive_upload
from app.tenant.context import TenantContext, require_tenant_context
from app.tenant.features import require_bulk_import_enabled

router = APIRouter(prefix="/api/erp", tags=["erp"])


def _expected_webhook_token(tenant_ctx: TenantContext) -> str:
    return tenant_ctx.settings.erp_webhook_token.strip()


def _authorize_webhook(authorization: str | None, tenant_ctx: TenantContext) -> bool:
    expected = _expected_webhook_token(tenant_ctx)
    if not expected:
        return False
    if not authorization or not authorization.startswith("Bearer "):
        return False
    supplied = authorization.removeprefix("Bearer ").strip()
    return hmac.compare_digest(supplied, expected)


@router.post("/import")
async def erp_import_webhook(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    _enabled: None = Depends(require_bulk_import_enabled),
) -> JSONResponse:
    tenant_ctx = require_tenant_context()
    if not _authorize_webhook(authorization, tenant_ctx):
        return JSONResponse(
            {"detail": "Invalid webhook credentials."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
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
            from app.import_.history import record_import_run
            from app.models import ImportRunStatus

            report = {
                "valid": False,
                "applied": False,
                "errors": [{"error": err} for err in extract_errors],
            }
            record_import_run(
                db,
                tenant_id=tenant_ctx.tenant_id,
                status=ImportRunStatus.FAILED,
                source="webhook",
                report=report,
            )
            db.commit()
            return JSONResponse(report, status_code=status.HTTP_400_BAD_REQUEST)

        report = apply_directory(
            db,
            tenant_ctx.tenant_id,
            dest,
            source="webhook",
        )
        status_code = (
            status.HTTP_200_OK if report.get("applied") else status.HTTP_400_BAD_REQUEST
        )
        return JSONResponse(report, status_code=status_code)
