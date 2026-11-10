"""F07: parent in-app unread published-remark notices."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role
from app.parent_remark_notices import (
    list_unread_remark_previews,
    mark_remarks_read,
    unread_remark_count,
)
from app.tenant.features import (
    require_parent_portal,
    require_remarks_enabled,
)

router = APIRouter(
    prefix="/api/parent/remark-notices",
    tags=["parent-remark-notices"],
    dependencies=[Depends(require_roles(Role.PARENT))],
)


def _guard_parent_remarks(scope: AccessScope) -> None:
    require_parent_portal()
    require_remarks_enabled()
    if scope.role is not Role.PARENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only guardians may access remark notices.",
        )


@router.get("/count")
def remark_notices_count(
    scope: AccessScope = Depends(get_access_scope),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    _guard_parent_remarks(scope)
    return {"count": unread_remark_count(db, scope)}


@router.get("")
def remark_notices_list(
    scope: AccessScope = Depends(get_access_scope),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _guard_parent_remarks(scope)
    items = list_unread_remark_previews(db, scope)
    return {"items": items, "count": unread_remark_count(db, scope)}


class MarkReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remark_ids: list[int] | None = Field(default=None, max_length=200)
    mark_all: bool = False


@router.post("/mark-read")
def remark_notices_mark_read(
    body: MarkReadBody,
    scope: AccessScope = Depends(get_access_scope),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    _guard_parent_remarks(scope)
    if body.mark_all:
        marked = mark_remarks_read(db, scope, mark_all=True)
    elif body.remark_ids:
        marked = mark_remarks_read(db, scope, remark_ids=body.remark_ids)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide remark_ids or mark_all.",
        )
    return {"marked": marked, "count": unread_remark_count(db, scope)}
