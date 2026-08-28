"""Request dependencies: authentication, and the row-level access scope.

Authorization lives here rather than in routes or templates. Every analytics query
receives an `AccessScope` that already encodes exactly which students, sections and
subjects the caller may read, so a handler cannot accidentally widen access.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.db import get_db
from app.models import (
    AcademicYear,
    Role,
    Section,
    Student,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from app.schemas import FilterParams
from app.security import read_session_token


class NotAuthenticatedError(Exception):
    """Raised when a request has no usable session. Handled app-wide in main.py."""


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    token = request.cookies.get(settings.session_cookie_name, "")
    payload = read_session_token(token)
    if payload is None:
        raise NotAuthenticatedError
    user = db.get(User, payload["uid"])
    if user is None or not user.is_active:
        raise NotAuthenticatedError
    # A role change since the cookie was issued invalidates the session.
    if payload.get("role") != user.role.value:
        raise NotAuthenticatedError
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    try:
        return get_current_user(request, db)
    except NotAuthenticatedError:
        return None


def require_roles(*roles: Role):
    """Dependency factory restricting a route to the given roles."""
    allowed = frozenset(roles)

    def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not have access to this portal.",
            )
        return user

    return _guard


@dataclass(frozen=True)
class AccessScope:
    """The set of records a caller may read.

    A `None` collection means unrestricted (administrators only). Any other value is
    an explicit allow-list, and the `assert_*` helpers refuse anything outside it.

    `section_subjects` is the finest-grained rule: a teacher who teaches only
    Mathematics to 7-B may read Mathematics in 7-B, not Science in 7-B. It is `None`
    for callers whose access is bounded by student instead (parents and students).
    """

    user: User
    academic_year: AcademicYear | None
    student_ids: frozenset[int] | None
    section_ids: frozenset[int] | None
    subject_ids: frozenset[int] | None
    section_subjects: dict[int, frozenset[int]] | None = None
    teacher: Teacher | None = None
    students: tuple[Student, ...] = field(default_factory=tuple)
    aggregate_only: bool = False

    @property
    def role(self) -> Role:
        return self.user.role

    @property
    def is_admin(self) -> bool:
        return self.user.role is Role.ADMIN

    @property
    def academic_year_id(self) -> int | None:
        return self.academic_year.id if self.academic_year else None

    def _check(
        self, allowed: frozenset[int] | None, value: int | None, label: str
    ) -> int | None:
        if value is None or allowed is None:
            return value
        if value not in allowed:
            # Deliberately identical to a missing record so the response cannot be
            # used to probe for the existence of other students or sections.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found."
            )
        return value

    def assert_student(self, student_id: int | None) -> int | None:
        return self._check(self.student_ids, student_id, "Student")

    def assert_section(self, section_id: int | None) -> int | None:
        return self._check(self.section_ids, section_id, "Section")

    def assert_subject(self, subject_id: int | None) -> int | None:
        return self._check(self.subject_ids, subject_id, "Subject")

    def narrow(self, filters: FilterParams) -> FilterParams:
        """Validate a filter set against this scope and pin it to the resolved year."""
        self.assert_student(filters.student_id)
        self.assert_section(filters.section_id)
        self.assert_subject(filters.subject_id)
        updates: dict[str, object] = {}
        if filters.academic_year_id is None and self.academic_year_id is not None:
            updates["academic_year_id"] = self.academic_year_id
        return filters.model_copy(update=updates) if updates else filters

    def default_student(self) -> Student:
        if not self.students:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No student records are linked to this account.",
            )
        return self.students[0]

    def resolve_student(self, student_id: int | None) -> Student:
        """Pick the student to display, refusing any id outside the caller's scope."""
        if student_id is None:
            return self.default_student()
        self.assert_student(student_id)
        for student in self.students:
            if student.id == student_id:
                return student
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )

    def cohort_view(self) -> AccessScope:
        """A widened scope for computing class benchmarks only.

        A parent needs their child's class average and rank, which cannot be derived
        from their child's marks alone. This view opens the child's own classroom, but
        marks itself `aggregate_only` so any query that would expose a named classmate
        refuses to run against it (see `assert_identifiable`).
        """
        if self.role in (Role.ADMIN, Role.TEACHER):
            return self
        section_ids = frozenset(s.section_id for s in self.students)
        return replace(
            self,
            student_ids=None,
            section_ids=section_ids,
            section_subjects=None,
            aggregate_only=True,
        )

    def assert_identifiable(self) -> None:
        """Guard for queries that return student names rather than aggregates."""
        if self.aggregate_only:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This view may only return aggregated results.",
            )

    def resolve_section_id(self, section_id: int | None) -> int:
        if section_id is not None:
            self.assert_section(section_id)
            return section_id
        if self.section_ids:
            return sorted(self.section_ids)[0]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No classroom is linked to this account.",
        )


def _current_academic_year(
    db: Session, requested_id: int | None = None
) -> AcademicYear | None:
    if requested_id is not None:
        year = db.get(AcademicYear, requested_id)
        if year is not None:
            return year
    stmt = (
        select(AcademicYear)
        .order_by(AcademicYear.is_current.desc(), AcademicYear.start_date.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _students_in_sections(db: Session, section_ids: Iterable[int]) -> list[Student]:
    ids = list(section_ids)
    if not ids:
        return []
    stmt = (
        select(Student)
        .where(Student.section_id.in_(ids), Student.is_active.is_(True))
        .order_by(Student.section_id, Student.roll_no)
    )
    return list(db.scalars(stmt))


def get_access_scope(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AccessScope:
    """Build the caller's access scope from their role and their own links."""
    requested_year = request.query_params.get("academic_year_id")
    year_id: int | None = None
    if requested_year is not None and requested_year.isdigit():
        year_id = int(requested_year)
    academic_year = _current_academic_year(db, year_id)

    if user.role is Role.ADMIN:
        return AccessScope(
            user=user,
            academic_year=academic_year,
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )

    if user.role is Role.TEACHER:
        teacher = db.scalars(
            select(Teacher)
            .where(Teacher.user_id == user.id)
            .options(joinedload(Teacher.assignments))
        ).unique().first()
        if teacher is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account is not linked to a teacher record.",
            )
        assignment_rows = db.execute(
            select(TeacherAssignment.section_id, TeacherAssignment.subject_id).where(
                TeacherAssignment.teacher_id == teacher.id
            )
        ).all()
        section_subjects: dict[int, set[int]] = {}
        for section_id, subject_id in assignment_rows:
            section_subjects.setdefault(section_id, set()).add(subject_id)

        # A class teacher also oversees every subject in their own homeroom.
        homeroom_ids = set(
            db.scalars(select(Section.id).where(Section.class_teacher_id == teacher.id))
        )
        if homeroom_ids:
            all_subject_ids = set(db.scalars(select(Subject.id)))
            for section_id in homeroom_ids:
                section_subjects.setdefault(section_id, set()).update(all_subject_ids)

        section_ids = set(section_subjects)
        subject_ids: set[int] = set()
        for subjects in section_subjects.values():
            subject_ids |= subjects
        students = _students_in_sections(db, section_ids)
        return AccessScope(
            user=user,
            academic_year=academic_year,
            student_ids=frozenset(s.id for s in students),
            section_ids=frozenset(section_ids),
            subject_ids=frozenset(subject_ids),
            section_subjects={
                sid: frozenset(subs) for sid, subs in section_subjects.items()
            },
            teacher=teacher,
            students=tuple(students),
        )

    if user.role is Role.PARENT:
        children = list(
            db.scalars(
                select(Student)
                .where(Student.guardian_user_id == user.id, Student.is_active.is_(True))
                .order_by(Student.full_name)
            )
        )
        return AccessScope(
            user=user,
            academic_year=academic_year,
            student_ids=frozenset(c.id for c in children),
            section_ids=frozenset(c.section_id for c in children),
            subject_ids=None,
            students=tuple(children),
        )

    # Student: self only.
    self_record = db.scalars(
        select(Student).where(Student.user_id == user.id, Student.is_active.is_(True))
    ).first()
    if self_record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not linked to a student record.",
        )
    return AccessScope(
        user=user,
        academic_year=academic_year,
        student_ids=frozenset({self_record.id}),
        section_ids=frozenset({self_record.section_id}),
        subject_ids=None,
        students=(self_record,),
    )
