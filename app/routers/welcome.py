"""F04 welcome dismiss API and Help → portal tour."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope
from app.portals import PORTAL_HOME
from app.welcome import WELCOME_REOPEN_QUERY, set_welcome_dismissed_cookie

router = APIRouter(tags=["welcome"])
_api = APIRouter(prefix="/api/welcome", tags=["welcome"])


@_api.post("/dismiss")
def dismiss_welcome(
    request: Request,
    scope: AccessScope = Depends(get_access_scope),
    _db: Session = Depends(get_db),
) -> JSONResponse:
    response = JSONResponse({"dismissed": True})
    set_welcome_dismissed_cookie(response, scope.user)
    return response


@router.get("/help/welcome")
def help_portal_tour(
    scope: AccessScope = Depends(get_access_scope),
) -> RedirectResponse:
    """Re-open the role-aware welcome dialog on the portal home."""
    home = PORTAL_HOME[scope.role]
    return RedirectResponse(
        url=f"{home}?{WELCOME_REOPEN_QUERY}=1",
        status_code=302,
    )


router.include_router(_api)
