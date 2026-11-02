"""Parental consent API (DPDP Act pilot groundwork)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import (
    ConsentPurpose,
    ConsentStatus,
    ParentalConsentRecord,
    Role,
    Student,
    User,
)

router = APIRouter(prefix="/api/consent", tags=["consent"])


class ConsentRecordCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    student_id: int = Field(ge=1, le=2_000_000_000)
    purpose: ConsentPurpose
    status: ConsentStatus
    privacy_notice_version: str = Field(min_length=1, max_length=32)
    guardian_user_id: int | None = Field(default=None, ge=1, le=2_000_000_000)
    dpa_reference: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)


class ConsentPurposeStatus(BaseModel):
    purpose: ConsentPurpose
    status: ConsentStatus | None
    privacy_notice_version: str | None
    recorded_at: datetime | None
    guardian_user_id: int | None
    dpa_reference: str | None


class StudentConsentStatus(BaseModel):
    student_id: int
    purposes: list[ConsentPurposeStatus]


class GuardianConsentSummary(BaseModel):
    guardian_user_id: int
    students: list[StudentConsentStatus]


def _require_student_in_scope(scope: AccessScope, student_id: int) -> None:
    if scope.student_ids is not None and student_id not in scope.student_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def _load_student(db: Session, scope: AccessScope, student_id: int) -> Student:
    _require_student_in_scope(scope, student_id)
    student = db.scalars(
        select(Student).where(
            Student.id == student_id,
            Student.tenant_id == scope.tenant_id,
        )
    ).first()
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return student


def _latest_by_purpose(
    records: list[ParentalConsentRecord],
) -> dict[ConsentPurpose, ParentalConsentRecord]:
    latest: dict[ConsentPurpose, ParentalConsentRecord] = {}
    for record in sorted(records, key=lambda row: row.recorded_at):
        latest[record.purpose] = record
    return latest


def _student_status_from_records(
    student_id: int, records: list[ParentalConsentRecord]
) -> StudentConsentStatus:
    latest = _latest_by_purpose(records)
    purposes: list[ConsentPurposeStatus] = []
    for purpose in ConsentPurpose:
        row = latest.get(purpose)
        purposes.append(
            ConsentPurposeStatus(
                purpose=purpose,
                status=row.status if row else None,
                privacy_notice_version=row.privacy_notice_version if row else None,
                recorded_at=row.recorded_at if row else None,
                guardian_user_id=row.guardian_user_id if row else None,
                dpa_reference=row.dpa_reference if row else None,
            )
        )
    return StudentConsentStatus(student_id=student_id, purposes=purposes)


@router.post("/records", status_code=status.HTTP_201_CREATED)
def record_consent(
    body: ConsentRecordCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    actor: User = Depends(require_roles(Role.ADMIN, Role.PARENT)),
) -> ConsentPurposeStatus:
    student = _load_student(db, scope, body.student_id)

    guardian_user_id = body.guardian_user_id
    if actor.role is Role.PARENT:
        if student.guardian_user_id != actor.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
            )
        guardian_user_id = actor.id
    elif guardian_user_id is not None:
        guardian = db.scalars(
            select(User).where(
                User.id == guardian_user_id,
                User.tenant_id == scope.tenant_id,
                User.role == Role.PARENT,
            )
        ).first()
        if guardian is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="guardian_user_id must reference an active parent account.",
            )
        if student.guardian_user_id not in (None, guardian_user_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Student is linked to a different guardian.",
            )

    row = ParentalConsentRecord(
        tenant_id=scope.tenant_id,
        student_id=student.id,
        guardian_user_id=guardian_user_id,
        purpose=body.purpose,
        status=body.status,
        privacy_notice_version=body.privacy_notice_version,
        dpa_reference=body.dpa_reference,
        notes=body.notes,
        recorded_by_user_id=actor.id,
        recorded_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ConsentPurposeStatus(
        purpose=row.purpose,
        status=row.status,
        privacy_notice_version=row.privacy_notice_version,
        recorded_at=row.recorded_at,
        guardian_user_id=row.guardian_user_id,
        dpa_reference=row.dpa_reference,
    )


@router.get("/students/{student_id}", response_model=StudentConsentStatus)
def consent_status_for_student(
    student_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _user: User = Depends(require_roles(Role.ADMIN, Role.PARENT, Role.STUDENT)),
) -> StudentConsentStatus:
    _load_student(db, scope, student_id)
    records = list(
        db.scalars(
            select(ParentalConsentRecord).where(
                ParentalConsentRecord.tenant_id == scope.tenant_id,
                ParentalConsentRecord.student_id == student_id,
            )
        )
    )
    return _student_status_from_records(student_id, records)


@router.get("/guardians/me", response_model=GuardianConsentSummary)
def consent_status_for_guardian(
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    user: User = Depends(require_roles(Role.PARENT)),
) -> GuardianConsentSummary:
    student_ids = sorted(scope.student_ids)
    if not student_ids:
        return GuardianConsentSummary(guardian_user_id=user.id, students=[])

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

    return GuardianConsentSummary(
        guardian_user_id=user.id,
        students=[
            _student_status_from_records(sid, by_student.get(sid, []))
            for sid in student_ids
        ],
    )
