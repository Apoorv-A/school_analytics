"""Teacher remark submission API."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.audit import SecurityAuditAction, SecurityAuditTarget, record_security_event
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.import_.conflicts import ANALYTICS_SOURCE_SYSTEM
from app.models import Remark, RemarkCategory, RemarkVisibility, Role, Student, Term
from app.tenant.features import require_remarks_enabled

router = APIRouter(prefix="/api/remarks", tags=["remarks"])


class RemarkCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    student_id: int = Field(ge=1, le=2_000_000_000)
    body: str = Field(min_length=1, max_length=4000)
    category: RemarkCategory = RemarkCategory.ACADEMIC
    term_id: int | None = Field(default=None, ge=1, le=2_000_000_000)
    subject_id: int | None = Field(default=None, ge=1, le=2_000_000_000)
    publish: bool = False


def _remark_json(remark: Remark, *, include_staff_fields: bool) -> dict:
    payload = {
        "id": remark.id,
        "student_id": remark.student_id,
        "category": remark.category.value,
        "created_at": remark.created_at.isoformat(),
    }
    if include_staff_fields:
        payload["visibility"] = remark.visibility.value
        if remark.published_at is not None:
            payload["published_at"] = remark.published_at.isoformat()
    return payload


def _mark_remark_published(
    remark: Remark, *, actor_user_id: int, at: datetime | None = None
) -> None:
    when = at or datetime.now(UTC)
    remark.visibility = RemarkVisibility.PUBLISHED
    remark.published_at = when
    remark.published_by_user_id = actor_user_id


@router.post("")
def create_remark(
    payload: RemarkCreate,
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _teacher: object = Depends(require_roles(Role.TEACHER)),
) -> dict:
    require_remarks_enabled()
    scope.assert_student(payload.student_id)

    student = db.get(Student, payload.student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )
    scope.assert_tenant_record(student.tenant_id, "Student")
    scope.assert_section_subject(student.section_id, payload.subject_id)

    if payload.term_id is not None:
        term = db.get(Term, payload.term_id)
        if term is None or term.tenant_id != scope.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Term not found."
            )

    teacher = scope.teacher
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not linked to a teacher record.",
        )

    remark = Remark(
        tenant_id=scope.tenant_id,
        student_id=student.id,
        teacher_id=teacher.id,
        term_id=payload.term_id,
        subject_id=payload.subject_id,
        category=payload.category,
        body=payload.body,
        source_system=ANALYTICS_SOURCE_SYSTEM,
        visibility=(
            RemarkVisibility.PUBLISHED if payload.publish else RemarkVisibility.DRAFT
        ),
    )
    if payload.publish:
        _mark_remark_published(remark, actor_user_id=scope.user.id)
    db.add(remark)
    db.commit()
    db.refresh(remark)
    if payload.publish:
        record_security_event(
            db,
            tenant_id=scope.tenant_id,
            action=SecurityAuditAction.REMARK_PUBLISHED,
            target_type=SecurityAuditTarget.REMARK,
            target_id=remark.id,
            actor_user_id=scope.user.id,
            metadata={
                "student_id": remark.student_id,
                "category": remark.category.value,
            },
            request=request,
        )
        db.commit()
    return _remark_json(remark, include_staff_fields=True)


@router.post("/{remark_id}/publish")
def publish_remark(
    remark_id: int,
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _teacher: object = Depends(require_roles(Role.TEACHER)),
) -> dict:
    require_remarks_enabled()
    if remark_id < 1 or remark_id > 2_000_000_000:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Remark not found."
        )

    remark = db.get(Remark, remark_id)
    if remark is None or remark.tenant_id != scope.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Remark not found."
        )
    scope.assert_student(remark.student_id)

    student = db.get(Student, remark.student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Remark not found."
        )
    scope.assert_section_subject(student.section_id, remark.subject_id)

    if remark.visibility is RemarkVisibility.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remark is already published.",
        )

    _mark_remark_published(remark, actor_user_id=scope.user.id)
    db.commit()
    db.refresh(remark)
    record_security_event(
        db,
        tenant_id=scope.tenant_id,
        action=SecurityAuditAction.REMARK_PUBLISHED,
        target_type=SecurityAuditTarget.REMARK,
        target_id=remark.id,
        actor_user_id=scope.user.id,
        metadata={
            "student_id": remark.student_id,
            "category": remark.category.value,
        },
        request=request,
    )
    db.commit()
    return _remark_json(remark, include_staff_fields=True)
