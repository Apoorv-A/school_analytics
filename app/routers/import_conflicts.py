"""Admin API for resolving ERP import field conflicts."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.orm import Session

from app.audit import (
    SecurityAuditAction,
    SecurityAuditTarget,
    record_security_event,
)
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.import_.conflicts import list_import_conflicts, resolve_import_conflict
from app.models import ImportConflictStatus, Role
from app.tenant.features import require_bulk_import_enabled

router = APIRouter(prefix="/api/import/conflicts", tags=["import"])


class ConflictResolveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    strategy: Literal["keep_local", "accept_erp", "per_field"]
    field_choices: dict[str, Literal["local", "incoming"]] | None = None

    @model_validator(mode="after")
    def _validate_strategy_fields(self) -> ConflictResolveRequest:
        if self.strategy == "per_field" and not self.field_choices:
            raise ValueError("field_choices is required when strategy is per_field")
        if self.strategy != "per_field" and self.field_choices:
            raise ValueError("field_choices is only allowed for per_field strategy")
        return self


def _serialize_conflict(item) -> dict:
    return {
        "id": item.id,
        "entity_type": item.entity_type.value,
        "entity_id": item.entity_id,
        "external_id": item.external_id,
        "source": item.source,
        "local_values": item.local_values,
        "incoming_values": item.incoming_values,
        "field_names": item.field_names,
        "status": item.status.value,
        "resolution": item.resolution,
        "resolved_by_user_id": item.resolved_by_user_id,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


_STRATEGY_TO_STATUS = {
    "keep_local": ImportConflictStatus.KEEP_LOCAL,
    "accept_erp": ImportConflictStatus.ACCEPT_ERP,
    "per_field": ImportConflictStatus.PER_FIELD,
}


@router.get("")
def list_conflicts(
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
    _enabled: None = Depends(require_bulk_import_enabled),
    status: ImportConflictStatus | None = Query(
        default=ImportConflictStatus.PENDING
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    items = list_import_conflicts(
        db, scope.tenant_id, status=status, limit=limit
    )
    return {"items": [_serialize_conflict(item) for item in items]}


@router.post("/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: int,
    body: ConflictResolveRequest,
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
    _enabled: None = Depends(require_bulk_import_enabled),
) -> dict:
    try:
        row = resolve_import_conflict(
            db,
            tenant_id=scope.tenant_id,
            conflict_id=conflict_id,
            strategy=_STRATEGY_TO_STATUS[body.strategy],
            resolved_by_user_id=scope.user.id,
            field_choices=body.field_choices,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    record_security_event(
        db,
        tenant_id=scope.tenant_id,
        actor_user_id=scope.user.id,
        action=SecurityAuditAction.IMPORT_CONFLICT_RESOLVE,
        target_type=SecurityAuditTarget.IMPORT_CONFLICT,
        target_id=row.id,
        metadata={
            "strategy": body.strategy,
            "entity_type": row.entity_type.value,
            "external_id": row.external_id,
        },
        request=request,
    )
    db.commit()
    return {"item": _serialize_conflict(row)}
