"""Middleware enforcing forced password change after tenant context is set."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.db import SessionLocal
from app.deps import NotAuthenticatedError, get_current_user

_PASSWORD_CHANGE_EXEMPT_PREFIXES = (
    "/change-password",
    "/logout",
    "/login",
    "/forgot-password",
    "/reset-password",
    "/static/",
    "/healthz",
    "/readyz",
    "/favicon.ico",
    "/api/auth/",
)


class EnforcePasswordChangeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path == p or path.startswith(p) for p in _PASSWORD_CHANGE_EXEMPT_PREFIXES):
            return await call_next(request)
        try:
            with SessionLocal() as db:
                user = get_current_user(request, db)
        except NotAuthenticatedError:
            return await call_next(request)
        if not user.must_change_password:
            return await call_next(request)
        if path.startswith(("/api/", "/export/")):
            return JSONResponse(
                {"detail": "Password change required."},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return RedirectResponse("/change-password", status_code=status.HTTP_303_SEE_OTHER)
