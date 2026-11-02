"""Admin API for resolving queued assessment import mappings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.audit import (
    SecurityAuditAction,
    SecurityAuditTarget,
    record_security_event,
)
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.import_.mapping_queue import (
    list_mapping_queue,
    reject_mapping,
    resolve_mapping_to_assessment,
)
from app.models import ImportMappingQueueStatus, Role
from app.tenant.features import require_bulk_import_enabled

router = APIRouter(prefix="/api/import/mapping-queue", tags=["import"])


class MappingResolveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    assessment_id: int = Field(ge=1, le=2_000_000_000)


def _serialize_queue_item(item) -> dict:
    return {
        "id": item.id,
        "source": item.source,
        "external_key": item.external_key,
        "payload_snippet": item.payload_snippet,
        "status": item.status.value,
        "mapped_assessment_id": item.mapped_assessment_id,
        "resolved_by_user_id": item.resolved_by_user_id,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


@router.get("")
def list_queue(
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
    _enabled: None = Depends(require_bulk_import_enabled),
    status: ImportMappingQueueStatus | None = Query(
        default=ImportMappingQueueStatus.PENDING
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    items = list_mapping_queue(db, scope.tenant_id, status=status, limit=limit)
    return {"items": [_serialize_queue_item(item) for item in items]}


@router.post("/{queue_id}/map")
def map_queue_item(
    queue_id: int,
    body: MappingResolveRequest,
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
    _enabled: None = Depends(require_bulk_import_enabled),
) -> dict:
    try:
        queue_row, assessment = resolve_mapping_to_assessment(
            db,
            tenant_id=scope.tenant_id,
            queue_id=queue_id,
            assessment_id=body.assessment_id,
            resolved_by_user_id=scope.user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    record_security_event(
        db,
        tenant_id=scope.tenant_id,
        actor_user_id=scope.user.id,
        action=SecurityAuditAction.IMPORT_MAPPING_MAP,
        target_type=SecurityAuditTarget.IMPORT_MAPPING,
        target_id=queue_row.id,
        metadata={
            "external_key": queue_row.external_key,
            "assessment_id": assessment.id,
        },
        request=request,
    )
    db.commit()
    return {
        "item": _serialize_queue_item(queue_row),
        "assessment_id": assessment.id,
        "external_id": assessment.external_id,
    }


@router.post("/{queue_id}/reject")
def reject_queue_item(
    queue_id: int,
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
    _enabled: None = Depends(require_bulk_import_enabled),
) -> dict:
    try:
        queue_row = reject_mapping(
            db,
            tenant_id=scope.tenant_id,
            queue_id=queue_id,
            resolved_by_user_id=scope.user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    record_security_event(
        db,
        tenant_id=scope.tenant_id,
        actor_user_id=scope.user.id,
        action=SecurityAuditAction.IMPORT_MAPPING_REJECT,
        target_type=SecurityAuditTarget.IMPORT_MAPPING,
        target_id=queue_row.id,
        metadata={"external_key": queue_row.external_key},
        request=request,
    )
    db.commit()
    return {"item": _serialize_queue_item(queue_row)}
