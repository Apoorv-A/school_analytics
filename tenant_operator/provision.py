"""Idempotent tenant provisioning from validated configuration."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models import Role, School, Tenant, TenantDomain, User
from app.security import hash_password
from tenant_config.models import TenantConfigDocument


def _bootstrap_admin(
    db, tenant_id: uuid.UUID, email: str, password: str, full_name: str
) -> None:
    existing = db.scalars(
        select(User).where(User.tenant_id == tenant_id, User.email == email)
    ).first()
    if existing is not None:
        return
    db.add(
        User(
            tenant_id=tenant_id,
            email=email,
            full_name=full_name,
            role=Role.ADMIN,
            password_hash=hash_password(password),
        )
    )


@contextmanager
def _operator_session(database_url: str | None):
    """Open a session against the requested database, not the import-time default."""
    if database_url is None:
        with SessionLocal() as db:
            yield db
        return

    engine = create_engine(database_url, future=True)
    factory: Callable[[], Session] = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    with factory() as db:
        yield db
    engine.dispose()


def reconcile_tenant(
    doc: TenantConfigDocument, database_url: str | None = None
) -> dict[str, str]:
    bootstrap_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
    bootstrap_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    bootstrap_name = os.environ.get("BOOTSTRAP_ADMIN_NAME", "School Administrator").strip()

    with _operator_session(database_url) as db:
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
            "portal": doc.portal.model_dump(),
            "consent": doc.consent.model_dump(),
            "attendance": doc.attendance.model_dump(),
            "auth": doc.auth.model_dump(),
            "erp": {
                "webhookSecretRef": doc.erp.webhookSecretRef,
                "webhookSigningSecretRef": doc.erp.webhookSigningSecretRef,
            },
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

        if bootstrap_email and bootstrap_password:
            _bootstrap_admin(
                db,
                tenant.id,
                bootstrap_email,
                bootstrap_password,
                bootstrap_name or "School Administrator",
            )

        db.commit()
        return {
            "tenantId": str(tenant.id),
            "tenantKey": tenant.key,
            "hostname": doc.tenant.hostname,
            "status": "reconciled",
        }
