"""Idempotent tenant provisioning from validated configuration."""

from __future__ import annotations

import os
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import School, Tenant, TenantDomain
from tenant_config.models import TenantConfigDocument


def reconcile_tenant(
    doc: TenantConfigDocument, database_url: str | None = None
) -> dict[str, str]:
    if database_url is not None:
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()

    with SessionLocal() as db:
        tenant = db.scalars(
            select(Tenant).where(Tenant.key == doc.tenant.key)
        ).first()
        if tenant is None:
            tenant = Tenant(
                id=uuid.uuid4(),
                key=doc.tenant.key,
                display_name=doc.tenant.displayName,
                status="active",
            )
            db.add(tenant)
            db.flush()
        else:
            tenant.display_name = doc.tenant.displayName
            tenant.status = "active"

        tenant.settings_json = {
            "academics": doc.academics.model_dump(),
            "features": doc.features.model_dump(),
            "auth": doc.auth.model_dump(),
        }

        domain = db.scalars(
            select(TenantDomain).where(TenantDomain.hostname == doc.tenant.hostname)
        ).first()
        if domain is None:
            db.add(
                TenantDomain(
                    tenant_id=tenant.id,
                    hostname=doc.tenant.hostname,
                    is_primary=True,
                )
            )
        elif domain.tenant_id != tenant.id:
            raise ValueError(
                f"hostname {doc.tenant.hostname} is already bound to another tenant"
            )

        school = db.scalars(
            select(School).where(School.tenant_id == tenant.id)
        ).first()
        if school is None:
            school = School(
                tenant_id=tenant.id,
                name=doc.school.name,
                city=doc.school.city,
                board=doc.school.board,
            )
            db.add(school)
        else:
            school.name = doc.school.name
            school.city = doc.school.city
            school.board = doc.school.board

        db.commit()
        return {
            "tenantId": str(tenant.id),
            "tenantKey": tenant.key,
            "hostname": doc.tenant.hostname,
            "status": "reconciled",
        }
