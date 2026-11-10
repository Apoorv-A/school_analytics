"""SQLAlchemy ORM models for the school analytics domain."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Role(enum.StrEnum):
    ADMIN = "admin"
    TEACHER = "teacher"
    PARENT = "parent"
    STUDENT = "student"

    @property
    def label(self) -> str:
        return {
            Role.ADMIN: "Principal / Administrator",
            Role.TEACHER: "Teacher",
            Role.PARENT: "Parent / Guardian",
            Role.STUDENT: "Student",
        }[self]


class AssessmentType(enum.StrEnum):
    UNIT_TEST = "unit_test"
    MID_TERM = "mid_term"
    FINAL = "final"
    ASSIGNMENT = "assignment"
    PROJECT = "project"

    @property
    def label(self) -> str:
        return {
            AssessmentType.UNIT_TEST: "Unit Test",
            AssessmentType.MID_TERM: "Mid Term",
            AssessmentType.FINAL: "Final Exam",
            AssessmentType.ASSIGNMENT: "Assignment",
            AssessmentType.PROJECT: "Project",
        }[self]


class AttendanceStatus(enum.StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"

    @property
    def label(self) -> str:
        return self.value.capitalize()

    @property
    def counts_as_present(self) -> bool:
        return self in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)


class RemarkCategory(enum.StrEnum):
    ACADEMIC = "academic"
    BEHAVIOUR = "behaviour"
    ACHIEVEMENT = "achievement"
    CONCERN = "concern"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class RemarkVisibility(enum.StrEnum):
    """Staff-only draft vs remark published to linked guardians."""

    DRAFT = "draft"
    PUBLISHED = "published"


class Tenant(Base):
    """A school customer on the platform."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    domains: Mapped[list[TenantDomain]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    schools: Mapped[list[School]] = relationship(back_populates="tenant")


class TenantDomain(Base):
    """Maps a hostname to a tenant for request routing."""

    __tablename__ = "tenant_domains"
    __table_args__ = (UniqueConstraint("hostname", name="uq_tenant_domain_hostname"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="domains")


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    board: Mapped[str | None] = mapped_column(String(60))

    tenant: Mapped[Tenant] = relationship(back_populates="schools")
    academic_years: Mapped[list[AcademicYear]] = relationship(back_populates="school")
    grades: Mapped[list[Grade]] = relationship(back_populates="school")
    subjects: Mapped[list[Subject]] = relationship(back_populates="school")


class AcademicYear(Base):
    __tablename__ = "academic_years"
    __table_args__ = (UniqueConstraint("school_id", "label", name="uq_year_per_school"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    school: Mapped[School] = relationship(back_populates="academic_years")
    terms: Mapped[list[Term]] = relationship(
        back_populates="academic_year", order_by="Term.sequence"
    )


class Term(Base):
    __tablename__ = "terms"
    __table_args__ = (
        UniqueConstraint("academic_year_id", "sequence", name="uq_term_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    academic_year_id: Mapped[int] = mapped_column(
        ForeignKey("academic_years.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    academic_year: Mapped[AcademicYear] = relationship(back_populates="terms")


class Grade(Base):
    """A year group, e.g. "Grade 6"."""

    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("school_id", "level", name="uq_grade_level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)

    school: Mapped[School] = relationship(back_populates="grades")
    sections: Mapped[list[Section]] = relationship(
        back_populates="grade", order_by="Section.name"
    )


class Section(Base):
    """A specific classroom within a grade for one academic year, e.g. "6-A"."""

    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint(
            "grade_id", "name", "academic_year_id", name="uq_section_per_year"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    grade_id: Mapped[int] = mapped_column(ForeignKey("grades.id", ondelete="CASCADE"))
    academic_year_id: Mapped[int] = mapped_column(
        ForeignKey("academic_years.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(10), nullable=False)
    room: Mapped[str | None] = mapped_column(String(40))
    class_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id", ondelete="SET NULL")
    )

    grade: Mapped[Grade] = relationship(back_populates="sections")
    academic_year: Mapped[AcademicYear] = relationship()
    class_teacher: Mapped[Teacher | None] = relationship(
        foreign_keys=[class_teacher_id], back_populates="class_teacher_of"
    )
    students: Mapped[list[Student]] = relationship(back_populates="section")

    @property
    def display_name(self) -> str:
        return f"{self.grade.name} - {self.name}"


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_subject_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)

    school: Mapped[School] = relationship(back_populates="subjects")


class User(Base):
    """A login. Every stakeholder authenticates through this table."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_email_per_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_system: Mapped[str | None] = mapped_column(String(40))
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    totp_secret_enc: Mapped[str | None] = mapped_column(String(512))
    totp_pending_secret_enc: Mapped[str | None] = mapped_column(String(512))
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    teacher: Mapped[Teacher | None] = relationship(back_populates="user", uselist=False)
    student: Mapped[Student | None] = relationship(
        back_populates="user", foreign_keys="Student.user_id", uselist=False
    )
    children: Mapped[list[Student]] = relationship(
        back_populates="guardian", foreign_keys="Student.guardian_user_id"
    )
    mfa_recovery_codes: Mapped[list[UserMfaRecoveryCode]] = relationship(
        back_populates="user",
        foreign_keys="UserMfaRecoveryCode.user_id",
        cascade="all, delete-orphan",
    )


class UserMfaRecoveryCode(Base):
    """Single-use MFA recovery code (stored as SHA-256 hash)."""

    __tablename__ = "user_mfa_recovery_codes"
    __table_args__ = (
        UniqueConstraint("user_id", "code_hash", name="uq_user_mfa_recovery_code_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped[User] = relationship(
        foreign_keys=[user_id], back_populates="mfa_recovery_codes"
    )


class PasswordResetToken(Base):
    """Single-use, expiring password reset token (stored as SHA-256 hash)."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class Teacher(Base):
    __tablename__ = "teachers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "employee_code", name="uq_teacher_code_per_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    employee_code: Mapped[str] = mapped_column(String(32), nullable=False)
    department: Mapped[str | None] = mapped_column(String(80))
    joined_on: Mapped[date | None] = mapped_column(Date)

    user: Mapped[User] = relationship(back_populates="teacher")
    assignments: Mapped[list[TeacherAssignment]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan"
    )
    class_teacher_of: Mapped[list[Section]] = relationship(
        back_populates="class_teacher", foreign_keys="Section.class_teacher_id"
    )

    @property
    def full_name(self) -> str:
        return self.user.full_name


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("tenant_id", "admission_no", name="uq_admission_per_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )
    guardian_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), index=True
    )
    admission_no: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_system: Mapped[str | None] = mapped_column(String(40))
    roll_no: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    enrolled_on: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User | None] = relationship(
        back_populates="student", foreign_keys=[user_id]
    )
    guardian: Mapped[User | None] = relationship(
        back_populates="children", foreign_keys=[guardian_user_id]
    )
    section: Mapped[Section] = relationship(back_populates="students")
    scores: Mapped[list[Score]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )


class TeacherAssignment(Base):
    """Which teacher teaches which subject to which section, for a given year."""

    __tablename__ = "teacher_assignments"
    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "section_id",
            "academic_year_id",
            name="uq_subject_section_year",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"))
    academic_year_id: Mapped[int] = mapped_column(
        ForeignKey("academic_years.id", ondelete="CASCADE")
    )

    teacher: Mapped[Teacher] = relationship(back_populates="assignments")
    subject: Mapped[Subject] = relationship()
    section: Mapped[Section] = relationship()


class Assessment(Base):
    """A single graded event: a unit test, exam, assignment, or project."""

    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint("max_marks > 0", name="ck_assessment_max_marks_positive"),
        Index("ix_assessment_section_subject", "section_id", "subject_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    assessment_type: Mapped[AssessmentType] = mapped_column(
        Enum(AssessmentType, native_enum=False), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"))
    term_id: Mapped[int] = mapped_column(
        ForeignKey("terms.id", ondelete="CASCADE"), index=True
    )
    max_marks: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    weightage: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    conducted_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_system: Mapped[str | None] = mapped_column(String(40))
    created_by_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id", ondelete="SET NULL")
    )

    subject: Mapped[Subject] = relationship()
    section: Mapped[Section] = relationship()
    term: Mapped[Term] = relationship()
    scores: Mapped[list[Score]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("assessment_id", "student_id", name="uq_score_per_student"),
        CheckConstraint(
            "marks_obtained IS NULL OR marks_obtained >= 0",
            name="ck_score_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    marks_obtained: Mapped[float | None] = mapped_column(Float)
    is_absent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_system: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(255))

    assessment: Mapped[Assessment] = relationship(back_populates="scores")
    student: Mapped[Student] = relationship(back_populates="scores")

    @property
    def percentage(self) -> float | None:
        if self.is_absent or self.marks_obtained is None:
            return None
        if self.assessment.max_marks <= 0:
            return None
        return round(self.marks_obtained / self.assessment.max_marks * 100, 2)


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint("student_id", "on_date", name="uq_attendance_per_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    term_id: Mapped[int | None] = mapped_column(
        ForeignKey("terms.id", ondelete="SET NULL"), index=True
    )
    on_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, native_enum=False), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_system: Mapped[str | None] = mapped_column(String(40))

    student: Mapped[Student] = relationship()


class Remark(Base):
    """A teacher's qualitative note about a student for a term."""

    __tablename__ = "remarks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id", ondelete="SET NULL")
    )
    term_id: Mapped[int | None] = mapped_column(ForeignKey("terms.id", ondelete="SET NULL"))
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    category: Mapped[RemarkCategory] = mapped_column(
        Enum(RemarkCategory, native_enum=False), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[RemarkVisibility] = mapped_column(
        Enum(RemarkVisibility, native_enum=False),
        nullable=False,
        default=RemarkVisibility.DRAFT,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_remarks_published_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_system: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    student: Mapped[Student] = relationship()
    teacher: Mapped[Teacher | None] = relationship()
    term: Mapped[Term | None] = relationship()
    subject: Mapped[Subject | None] = relationship()


class InterventionStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"

    @property
    def label(self) -> str:
        return {
            InterventionStatus.OPEN: "Open",
            InterventionStatus.IN_PROGRESS: "In progress",
            InterventionStatus.RESOLVED: "Resolved",
        }[self]


class InterventionPriority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class InterventionEventType(enum.StrEnum):
    CASE_CREATED = "case_created"
    OWNER_CHANGED = "owner_changed"
    PRIORITY_CHANGED = "priority_changed"
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    GUARDIAN_CONTACTED = "guardian_contacted"
    STATUS_CHANGED = "status_changed"
    OUTCOME_RECORDED = "outcome_recorded"
    ESCALATION_TRIGGERED = "escalation_triggered"
    CASE_CLOSED = "case_closed"


class ActionTaskStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            ActionTaskStatus.OPEN: "Open",
            ActionTaskStatus.IN_PROGRESS: "In progress",
            ActionTaskStatus.COMPLETED: "Completed",
            ActionTaskStatus.CANCELLED: "Cancelled",
        }[self]


class ActionTaskVisibility(enum.StrEnum):
    STAFF = "staff"
    ASSIGNEE_ONLY = "assignee_only"

    @property
    def label(self) -> str:
        return {
            ActionTaskVisibility.STAFF: "All case staff",
            ActionTaskVisibility.ASSIGNEE_ONLY: "Assignee only",
        }[self]


class InterventionContactType(enum.StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    IN_PERSON = "in_person"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            InterventionContactType.PHONE: "Phone",
            InterventionContactType.EMAIL: "Email",
            InterventionContactType.IN_PERSON: "In person",
            InterventionContactType.OTHER: "Other",
        }[self]


class ImportRunStatus(enum.StrEnum):
    VALIDATED = "validated"
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"


class ImportMappingQueueStatus(enum.StrEnum):
    PENDING = "pending"
    MAPPED = "mapped"
    REJECTED = "rejected"


class ImportConflictEntityType(enum.StrEnum):
    SCORE = "score"
    REMARK = "remark"
    ATTENDANCE = "attendance"


class ImportConflictStatus(enum.StrEnum):
    PENDING = "pending"
    KEEP_LOCAL = "keep_local"
    ACCEPT_ERP = "accept_erp"
    PER_FIELD = "per_field"


class InterventionCase(Base):
    """Actionable follow-up for an at-risk student."""

    __tablename__ = "intervention_cases"
    __table_args__ = (
        Index("ix_intervention_cases_tenant_status", "tenant_id", "status"),
        Index("ix_intervention_cases_student", "tenant_id", "student_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    signal_source: Mapped[str] = mapped_column(String(80), nullable=False)
    reason_summary: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[InterventionPriority] = mapped_column(
        Enum(InterventionPriority, native_enum=False), nullable=False
    )
    status: Mapped[InterventionStatus] = mapped_column(
        Enum(InterventionStatus, native_enum=False),
        nullable=False,
        default=InterventionStatus.OPEN,
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    notes_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    student: Mapped[Student] = relationship()
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id])
    contacts: Mapped[list[InterventionContact]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="InterventionContact.contacted_at.desc()",
    )
    events: Mapped[list[InterventionEvent]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="InterventionEvent.created_at.asc()",
    )
    tasks: Mapped[list[ActionTask]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="ActionTask.created_at.asc()",
    )


class InterventionEvent(Base):
    """Append-only timeline entry for an intervention case."""

    __tablename__ = "intervention_events"
    __table_args__ = (
        Index(
            "ix_intervention_events_case_created",
            "tenant_id",
            "intervention_case_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    intervention_case_id: Mapped[int] = mapped_column(
        ForeignKey("intervention_cases.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[InterventionEventType] = mapped_column(
        Enum(InterventionEventType, native_enum=False), nullable=False, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    case: Mapped[InterventionCase] = relationship(back_populates="events")


class InterventionContact(Base):
    """A contact attempt logged against an intervention case."""

    __tablename__ = "intervention_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("intervention_cases.id", ondelete="CASCADE"), index=True
    )
    contact_type: Mapped[InterventionContactType] = mapped_column(
        Enum(InterventionContactType, native_enum=False), nullable=False
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    contacted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    case: Mapped[InterventionCase] = relationship(back_populates="contacts")


class ActionTask(Base):
    """Follow-up action item linked to an intervention case."""

    __tablename__ = "action_tasks"
    __table_args__ = (
        Index(
            "ix_action_tasks_case_status",
            "tenant_id",
            "intervention_case_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    intervention_case_id: Mapped[int] = mapped_column(
        ForeignKey("intervention_cases.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_to_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    visibility: Mapped[ActionTaskVisibility] = mapped_column(
        Enum(ActionTaskVisibility, native_enum=False),
        nullable=False,
        default=ActionTaskVisibility.STAFF,
    )
    status: Mapped[ActionTaskStatus] = mapped_column(
        Enum(ActionTaskStatus, native_enum=False),
        nullable=False,
        default=ActionTaskStatus.OPEN,
    )
    priority: Mapped[InterventionPriority] = mapped_column(
        Enum(InterventionPriority, native_enum=False), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    case: Mapped[InterventionCase] = relationship(back_populates="tasks")
    assigned_to: Mapped[User | None] = relationship(foreign_keys=[assigned_to_user_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])


class ImportMappingQueue(Base):
    """Staging queue for external assessment keys that need explicit admin mapping."""

    __tablename__ = "import_mapping_queue"
    __table_args__ = (
        Index(
            "ix_import_mapping_queue_tenant_status",
            "tenant_id",
            "status",
        ),
        UniqueConstraint(
            "tenant_id",
            "external_key",
            name="uq_import_mapping_pending_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_snippet: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[ImportMappingQueueStatus] = mapped_column(
        Enum(ImportMappingQueueStatus, native_enum=False),
        nullable=False,
        default=ImportMappingQueueStatus.PENDING,
    )
    mapped_assessment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessments.id", ondelete="SET NULL"), index=True
    )
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    mapped_assessment: Mapped[Assessment | None] = relationship()


class ImportConflict(Base):
    """Pending operator resolution when ERP import disagrees with analytics-owned data."""

    __tablename__ = "import_conflicts"
    __table_args__ = (
        Index(
            "ix_import_conflicts_tenant_status",
            "tenant_id",
            "status",
        ),
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "entity_id",
            name="uq_import_conflicts_entity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[ImportConflictEntityType] = mapped_column(
        Enum(ImportConflictEntityType, native_enum=False), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    local_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    incoming_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    field_names: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[ImportConflictStatus] = mapped_column(
        Enum(ImportConflictStatus, native_enum=False),
        nullable=False,
        default=ImportConflictStatus.PENDING,
    )
    resolution: Mapped[dict | None] = mapped_column(JSON)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ImportSyncState(Base):
    """Last successfully applied bundle fingerprint for scheduled ERP sync."""

    __tablename__ = "import_sync_state"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source",
            name="uq_import_sync_state_tenant_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    bundle_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ImportRun(Base):
    """Audit trail for CSV ingest (Phase 2 foundation for ERP sync)."""

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[ImportRunStatus] = mapped_column(
        Enum(ImportRunStatus, native_enum=False), nullable=False
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    file_summary: Mapped[str | None] = mapped_column(String(255))
    row_counts: Mapped[dict | None] = mapped_column(JSON)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ConsentPurpose(enum.StrEnum):
    """Lawful processing purpose recorded for a minor's data (DPDP pilot)."""

    STUDENT_ANALYTICS = "student_analytics"
    DATA_PROCESSING = "data_processing"


class ConsentStatus(enum.StrEnum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


class ParentalConsentRecord(Base):
    """Append-only parental consent event for a student and processing purpose."""

    __tablename__ = "parental_consent_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    guardian_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    purpose: Mapped[ConsentPurpose] = mapped_column(
        Enum(ConsentPurpose, native_enum=False), nullable=False
    )
    status: Mapped[ConsentStatus] = mapped_column(
        Enum(ConsentStatus, native_enum=False), nullable=False
    )
    privacy_notice_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dpa_reference: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=False
    )

    student: Mapped[Student] = relationship(foreign_keys=[student_id])
    guardian: Mapped[User | None] = relationship(foreign_keys=[guardian_user_id])


class SecurityAuditEvent(Base):
    """Append-only audit trail for security-relevant identity and access actions."""

    __tablename__ = "security_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
