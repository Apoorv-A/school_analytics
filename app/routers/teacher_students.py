"""Teacher-scoped student search (F05).

Administrators use ``GET /api/students/search`` for the full school roster. Teachers
receive a separate endpoint so authorization stays explicit and search results never
widen beyond effective class assignments.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.analytics import queries
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role

router = APIRouter(prefix="/api/teacher/students", tags=["teacher-students"])


@router.get("/search")
def search_assigned_students(
    q: str = Query(min_length=0, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _teacher: object = Depends(require_roles(Role.TEACHER)),
) -> dict:
    """Prefix-search students the caller may view (name, admission number)."""
    results = queries.search_students(
        db, scope, q, limit=limit, include_contact_email=False
    )
    return {"results": results}
