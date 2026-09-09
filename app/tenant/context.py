"""Request-scoped tenant context."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from app.tenant.settings import TenantSettings

_tenant_ctx: ContextVar[TenantContext | None] = ContextVar("tenant_ctx", default=None)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    tenant_key: str
    display_name: str
    hostname: str
    settings: TenantSettings


def set_tenant_context(ctx: TenantContext | None) -> None:
    _tenant_ctx.set(ctx)


def get_tenant_context() -> TenantContext | None:
    return _tenant_ctx.get()


def require_tenant_context() -> TenantContext:
    ctx = get_tenant_context()
    if ctx is None:
        raise RuntimeError("Tenant context is not set for this request.")
    return ctx


def get_tenant_id() -> uuid.UUID | None:
    ctx = get_tenant_context()
    return ctx.tenant_id if ctx else None
