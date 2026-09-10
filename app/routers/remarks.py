"""Teacher remark submission API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Remark, RemarkCategory, Role, Student, Term
from app.tenant.features import require_remarks_enabled

router = APIRouter(prefix="/api/remarks", tags=["remarks"])


class RemarkCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    student_id: int = Field(ge=1, le=2_000_000_000)
    body: str = Field(min_length=1, max_length=4000)
    category: RemarkCategory = RemarkCategory.ACADEMIC
    term_id: int | None = Field(default=None, ge=1, le=2_000_000_000)
    subject_id: int | None = Field(default=None, ge=1, le=2_000_000_000)


@router.post("")
def create_remark(
    payload: RemarkCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _teacher: object = Depends(require_roles(Role.TEACHER)),
) -> dict:
    require_remarks_enabled()
    scope.assert_student(payload.student_id)
    if payload.subject_id is not None:
        scope.assert_subject(payload.subject_id)

    student = db.get(Student, payload.student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )
    scope.assert_tenant_record(student.tenant_id, "Student")

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
    )
    db.add(remark)
    db.commit()
    db.refresh(remark)
    return {
        "id": remark.id,
        "student_id": remark.student_id,
        "category": remark.category.value,
        "created_at": remark.created_at.isoformat(),
    }
