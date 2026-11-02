"""ERP webhook ingest (Phase 2 connector entry point)."""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.audit import SecurityAuditAction, SecurityAuditTarget, record_security_event
from app.db import get_db
from app.erp_webhook_security import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    extract_multipart_file,
    verify_webhook_request,
)
from app.import_.apply import apply_directory
from app.import_.limits import MAX_UPLOAD_BYTES
from app.import_.upload_io import extract_archive_upload
from app.secrets import SecretResolutionError, resolve_erp_webhook_signing_secret
from app.tenant.context import TenantContext, require_tenant_context
from app.tenant.features import require_bulk_import_enabled

router = APIRouter(prefix="/api/erp", tags=["erp"])


def _auth_failure_response(
    *,
    db: Session,
    tenant_ctx: TenantContext,
    request: Request,
    reason: str,
    status_code: int,
) -> JSONResponse:
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.WEBHOOK_AUTH_FAILURE,
        target_type=SecurityAuditTarget.WEBHOOK,
        target_id="import",
        metadata={"reason": reason},
        request=request,
    )
    db.commit()
    return JSONResponse(
        {"detail": "Invalid webhook credentials."},
        status_code=status_code,
    )


@router.post("/import")
async def erp_import_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _enabled: None = Depends(require_bulk_import_enabled),
) -> JSONResponse:
    tenant_ctx = require_tenant_context()
    body = await request.body()
    try:
        signing_secret = resolve_erp_webhook_signing_secret(
            webhook_secret_ref=tenant_ctx.settings.erp_webhook_secret_ref,
            webhook_signing_secret_ref=tenant_ctx.settings.erp_webhook_signing_secret_ref,
            required=True,
        )
    except SecretResolutionError:
        return _auth_failure_response(
            db=db,
            tenant_ctx=tenant_ctx,
            request=request,
            reason="missing_signing_key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    auth = verify_webhook_request(
        tenant_key=tenant_ctx.tenant_key,
        signing_secret=signing_secret,
        timestamp_header=request.headers.get(HEADER_TIMESTAMP),
        nonce_header=request.headers.get(HEADER_NONCE),
        signature_header=request.headers.get(HEADER_SIGNATURE),
        body=body,
    )
    if not auth.ok:
        return _auth_failure_response(
            db=db,
            tenant_ctx=tenant_ctx,
            request=request,
            reason=auth.failure.value if auth.failure else "invalid_signature",
            status_code=auth.status_code,
        )

    content_type = request.headers.get("content-type", "")
    extracted = extract_multipart_file(content_type, body)
    if extracted is None:
        return JSONResponse(
            {"applied": False, "errors": [{"error": "Missing file upload."}]},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    filename, content = extracted
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"applied": False, "errors": [{"error": "Upload exceeds 5 MB limit."}]},
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    upload = UploadFile(filename=filename, file=BytesIO(content))

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        extract_errors = extract_archive_upload(upload, dest)
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
