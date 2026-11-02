"""Parent portal page for recording privacy and analytics consent."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.consent.status import latest_by_purpose
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import ConsentPurpose, ParentalConsentRecord, Role, School
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

_PURPOSE_LABELS = {
    ConsentPurpose.STUDENT_ANALYTICS: "Student analytics",
    ConsentPurpose.DATA_PROCESSING: "Core data processing",
}


def _status_label(status: str | None) -> str:
    if status == "granted":
        return "Granted"
    if status == "withdrawn":
        return "Withdrawn"
    return "Not recorded"


def _consent_children_for_scope(
    db: Session, scope: AccessScope
) -> list[dict[str, object]]:
    student_ids = sorted(scope.student_ids or ())
    if not student_ids:
        return []

    records = list(
        db.scalars(
            select(ParentalConsentRecord).where(
                ParentalConsentRecord.tenant_id == scope.tenant_id,
                ParentalConsentRecord.student_id.in_(student_ids),
            )
        )
    )
    by_student: dict[int, list[ParentalConsentRecord]] = {sid: [] for sid in student_ids}
    for record in records:
        by_student.setdefault(record.student_id, []).append(record)

    children: list[dict[str, object]] = []
    for student in scope.students:
        latest = latest_by_purpose(by_student.get(student.id, []))
        purposes: list[dict[str, object]] = []
        for purpose in ConsentPurpose:
            row = latest.get(purpose)
            status_value = row.status.value if row else None
            purposes.append(
                {
                    "key": purpose.value,
                    "label": _PURPOSE_LABELS[purpose],
                    "status": status_value,
                    "status_label": _status_label(status_value),
                    "recorded_at": row.recorded_at.date().isoformat() if row else None,
                }
            )
        children.append(
            {
                "id": student.id,
                "name": student.full_name,
                "purposes": purposes,
            }
        )
    return children


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
            "consent_children": _consent_children_for_scope(db, scope),
        },
    )
