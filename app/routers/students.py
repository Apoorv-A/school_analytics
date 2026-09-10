"""Student lookup API (admin search)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.analytics import queries
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role

router = APIRouter(prefix="/api/students", tags=["students"])


@router.get("/search")
def search_students(
    q: str = Query(min_length=0, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _admin: object = Depends(require_roles(Role.ADMIN)),
) -> dict:
    if not scope.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators may search the full student roster.",
        )
    results = queries.search_students(db, scope, q, limit=limit)
    return {"results": results}
