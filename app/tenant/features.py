"""Tenant feature flag guards."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.tenant.context import require_tenant_context


def require_exports_enabled() -> None:
    if not require_tenant_context().settings.exports_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Exports are disabled for this school.",
        )


def require_parent_portal() -> None:
    if not require_tenant_context().settings.parent_portal_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def require_student_portal() -> None:
    if not require_tenant_context().settings.student_portal_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def require_remarks_enabled() -> None:
    if not require_tenant_context().settings.remarks_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Remarks are disabled for this school.",
        )


def require_bulk_import_enabled() -> None:
    if not require_tenant_context().settings.bulk_import_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found.",
        )
