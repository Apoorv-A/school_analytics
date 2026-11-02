"""Parent portal page for recording privacy and analytics consent."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role, School
from app.portals import PORTAL_HOME, portal_nav
from app.templating import templates
from app.tenant.context import require_tenant_context
from app.tenant.features import require_parent_portal, require_parental_consent_enabled

router = APIRouter(
    prefix="/parent",
    tags=["parent"],
    dependencies=[
        Depends(require_roles(Role.PARENT)),
        Depends(require_parent_portal),
    ],
)


@router.get("/consent")
def parent_consent_page(
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
) -> Response:
    require_parental_consent_enabled()
    tenant_ctx = require_tenant_context()
    school = db.scalars(
        select(School).where(School.tenant_id == scope.tenant_id)
    ).first()
    return templates.TemplateResponse(
        request,
        "parent_consent.html",
        {
            "request": request,
            "page_title": "Privacy & consent",
            "page_subtitle": "Manage how we use your child's school data",
            "active_nav": "/parent/consent",
            "portal_home": PORTAL_HOME[Role.PARENT],
            "nav_groups": portal_nav(scope.role, tenant_ctx.settings),
            "current_user": scope.user,
            "school": school,
            "visible_filters": [],
            "pinned_filters": {},
            "privacy_notice_version": tenant_ctx.settings.consent_privacy_notice_version,
            "children": [
                {"id": student.id, "name": student.full_name}
                for student in scope.students
            ],
        },
    )
