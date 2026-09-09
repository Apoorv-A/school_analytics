"""Resolve tenant from the HTTP Host header."""

from __future__ import annotations

import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.db import SessionLocal
from app.models import Tenant, TenantDomain
from app.tenant.context import TenantContext, set_tenant_context
from app.tenant.settings import TenantSettings

logger = logging.getLogger(__name__)

_EXEMPT_PREFIXES = ("/static/", "/healthz")


def _normalize_host(raw: str | None) -> str | None:
    if not raw:
        return None
    host = raw.split(",")[0].strip().lower()
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    return host or None


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        hostname = _normalize_host(request.headers.get("host"))
        if hostname is None:
            return JSONResponse(
                {"detail": "Not found."},
                status_code=status.HTTP_404_NOT_FOUND,
            )

        with SessionLocal() as db:
            domain = db.scalars(
                select(TenantDomain)
                .where(TenantDomain.hostname == hostname)
                .limit(1)
            ).first()
            if domain is None:
                return JSONResponse(
                    {"detail": "Not found."},
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            tenant = db.get(Tenant, domain.tenant_id)
            if tenant is None or tenant.status != "active":
                return JSONResponse(
                    {"detail": "Not found."},
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            ctx = TenantContext(
                tenant_id=tenant.id,
                tenant_key=tenant.key,
                display_name=tenant.display_name,
                hostname=hostname,
                settings=TenantSettings.from_json(tenant.settings_json),
            )

        set_tenant_context(ctx)
        request.state.tenant = ctx
        try:
            return await call_next(request)
        finally:
            set_tenant_context(None)
