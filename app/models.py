"""SQLAlchemy ORM models for the school analytics domain."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
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


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    board: Mapped[str | None] = mapped_column(String(60))

    academic_years: Mapped[list[AcademicYear]] = relationship(back_populates="school")
    grades: Mapped[list[Grade]] = relationship(back_populates="school")
    subjects: Mapped[list[Subject]] = relationship(back_populates="school")


class AcademicYear(Base):
    __tablename__ = "academic_years"
    __table_args__ = (UniqueConstraint("school_id", "label", name="uq_year_per_school"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)

    school: Mapped[School] = relationship(back_populates="subjects")


class User(Base):
    """A login. Every stakeholder authenticates through this table."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    teacher: Mapped[Teacher | None] = relationship(back_populates="user", uselist=False)
    student: Mapped[Student | None] = relationship(
        back_populates="user", foreign_keys="Student.user_id", uselist=False
    )
    children: Mapped[list[Student]] = relationship(
        back_populates="guardian", foreign_keys="Student.guardian_user_id"
    )


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    employee_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
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

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )
    guardian_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), index=True
    )
    admission_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
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
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    marks_obtained: Mapped[float | None] = mapped_column(Float)
    is_absent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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

    student: Mapped[Student] = relationship()


class Remark(Base):
    """A teacher's qualitative note about a student for a term."""

    __tablename__ = "remarks"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    student: Mapped[Student] = relationship()
    teacher: Mapped[Teacher | None] = relationship()
    term: Mapped[Term | None] = relationship()
    subject: Mapped[Subject | None] = relationship()
