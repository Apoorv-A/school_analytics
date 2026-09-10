"""Idempotent CSV apply for tenant data."""

from __future__ import annotations

import csv
import re
import secrets
import tempfile
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.import_.upload_io import write_named_uploads
from app.import_.validate import validate_directory
from app.models import (
    AcademicYear,
    Assessment,
    AssessmentType,
    Attendance,
    AttendanceStatus,
    Grade,
    Remark,
    RemarkCategory,
    Role,
    School,
    Score,
    Section,
    Student,
    Subject,
    Teacher,
    TeacherAssignment,
    Term,
    User,
)
from app.security import hash_password

ApplyWarning = dict[str, str]
ApplyResult = tuple[int, list[ApplyWarning]]
CreatedUserCredential = dict[str, str]
UsersApplyResult = tuple[int, list[ApplyWarning], list[CreatedUserCredential]]

_OPERATOR_SOURCES = frozenset({"cli", "upload"})


def _row_warning(file: str, row: int, message: str) -> ApplyWarning:
    return {"file": file, "row": str(row), "error": message}


def _parse_subject_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [code.strip() for code in raw.split(",") if code.strip()]


_ASSESSMENT_CODE = re.compile(
    r"^(?P<type>UT|MT|FN|AS|PJ)-(?P<subject>[A-Z0-9]+)-(?P<section>\d+[A-Za-z]+)-T(?P<term>\d+)$",
    re.IGNORECASE,
)
_ASSESSMENT_TYPE_PREFIX = {
    "UT": AssessmentType.UNIT_TEST,
    "MT": AssessmentType.MID_TERM,
    "FN": AssessmentType.FINAL,
    "AS": AssessmentType.ASSIGNMENT,
    "PJ": AssessmentType.PROJECT,
}


def _next_roll_no(db: Session, section_id: int) -> int:
    current = db.scalar(
        select(func.max(Student.roll_no)).where(Student.section_id == section_id)
    )
    return (current or 0) + 1


def _ensure_student(
    db: Session,
    tenant_id: uuid.UUID,
    school: School,
    *,
    user: User,
    admission_no: str,
    full_name: str,
    section_code: str | None,
    external_id: str | None,
    source_system: str | None,
) -> tuple[Student | None, str | None]:
    """Create a student row when users.csv introduces a new admission number."""
    section: Section | None = None
    if section_code:
        section = _resolve_section_code(db, tenant_id, school.id, section_code)
        if section is None:
            return None, f"section_code {section_code!r} not found"
    else:
        return None, "section_code is required to create a new student"

    student = Student(
        tenant_id=tenant_id,
        user_id=user.id,
        section_id=section.id,
        admission_no=admission_no,
        roll_no=_next_roll_no(db, section.id),
        full_name=full_name,
        external_id=external_id,
        source_system=source_system,
    )
    db.add(student)
    db.flush()
    return student, None


def _ensure_assessment(
    db: Session,
    tenant_id: uuid.UUID,
    school: School,
    *,
    assessment_code: str,
    student: Student,
    source_system: str | None,
) -> tuple[Assessment | None, str | None]:
    """Resolve or create an assessment keyed by assessment_code → external_id."""
    assessment = db.scalars(
        select(Assessment).where(
            Assessment.tenant_id == tenant_id,
            Assessment.external_id == assessment_code,
        )
    ).first()
    if assessment is not None:
        return assessment, None

    match = _ASSESSMENT_CODE.fullmatch(assessment_code.strip())
    if match is None:
        return None, f"Assessment code {assessment_code!r} not found"

    subject = db.scalars(
        select(Subject).where(
            Subject.school_id == school.id,
            Subject.code == match.group("subject").upper(),
        )
    ).first()
    if subject is None:
        return None, f"subject for assessment code {assessment_code!r} not found"

    section = _resolve_section_code(
        db, tenant_id, school.id, match.group("section")
    )
    if section is None:
        return None, f"section for assessment code {assessment_code!r} not found"

    term = _resolve_term_sequence(
        db, tenant_id, school.id, int(match.group("term"))
    )
    if term is None:
        return None, f"term for assessment code {assessment_code!r} not found"

    assessment_type = _ASSESSMENT_TYPE_PREFIX[match.group("type").upper()]
    assessment = Assessment(
        tenant_id=tenant_id,
        name=f"{subject.name} {assessment_type.label}",
        assessment_type=assessment_type,
        subject_id=subject.id,
        section_id=section.id,
        term_id=term.id,
        max_marks=100.0,
        conducted_on=term.start_date,
        external_id=assessment_code,
        source_system=source_system,
    )
    db.add(assessment)
    db.flush()
    return assessment, None


def _sanitize_report_for_audit(report: dict[str, object]) -> dict[str, object]:
    """Drop one-time credentials before persisting import run metadata."""
    sanitized = dict(report)
    sanitized.pop("created_users", None)
    return sanitized


def apply_directory(
    db: Session,
    tenant_id: uuid.UUID,
    directory: Path,
    *,
    source: str = "cli",
    user_id: int | None = None,
) -> dict[str, object]:
    from app.import_.history import record_import_run
    from app.models import ImportRunStatus

    report = validate_directory(directory)
    if not report["valid"]:
        record_import_run(
            db,
            tenant_id=tenant_id,
            status=ImportRunStatus.FAILED,
            source=source,
            report=report,
            user_id=user_id,
        )
        db.commit()
        return {**report, "applied": False}

    applied: dict[str, int] = {}
    warnings: list[ApplyWarning] = []
    created_users: list[CreatedUserCredential] = []
    structure_path = directory / "school_structure.csv"
    if structure_path.exists():
        applied["school_structure.csv"] = _apply_school_structure(
            db, tenant_id, structure_path
        )
    users_path = directory / "users.csv"
    if users_path.exists():
        count, user_warnings, new_users = _apply_users(db, tenant_id, users_path)
        applied["users.csv"] = count
        warnings.extend(user_warnings)
        created_users.extend(new_users)
    scores_path = directory / "scores.csv"
    if scores_path.exists():
        count, score_warnings = _apply_scores(db, tenant_id, scores_path)
        applied["scores.csv"] = count
        warnings.extend(score_warnings)
    attendance_path = directory / "attendance.csv"
    if attendance_path.exists():
        count, attendance_warnings = _apply_attendance(db, tenant_id, attendance_path)
        applied["attendance.csv"] = count
        warnings.extend(attendance_warnings)
    remarks_path = directory / "remarks.csv"
    if remarks_path.exists():
        count, remark_warnings = _apply_remarks(db, tenant_id, remarks_path)
        applied["remarks.csv"] = count
        warnings.extend(remark_warnings)

    db.commit()
    result: dict[str, object] = {
        "valid": True,
        "applied": True,
        "rows": applied,
        "errors": [],
        "warnings": warnings,
    }
    if warnings:
        result["partial"] = True
    if created_users and source in _OPERATOR_SOURCES:
        result["created_users"] = created_users
    record_import_run(
        db,
        tenant_id=tenant_id,
        status=ImportRunStatus.APPLIED,
        source=source,
        report=_sanitize_report_for_audit({**result, "summary": applied}),
        user_id=user_id,
    )
    db.commit()
    return result


def _current_academic_year(
    db: Session, tenant_id: uuid.UUID, school_id: int
) -> AcademicYear | None:
    return db.scalars(
        select(AcademicYear)
        .where(AcademicYear.tenant_id == tenant_id, AcademicYear.school_id == school_id)
        .order_by(AcademicYear.is_current.desc(), AcademicYear.start_date.desc())
        .limit(1)
    ).first()


def _tenant_school(db: Session, tenant_id: uuid.UUID) -> School | None:
    return db.scalars(select(School).where(School.tenant_id == tenant_id)).first()


def _resolve_section_code(
    db: Session, tenant_id: uuid.UUID, school_id: int, section_code: str
) -> Section | None:
    match = re.fullmatch(r"(\d+)([A-Za-z]+)", section_code.strip())
    if not match:
        return None
    level = int(match.group(1))
    name = match.group(2).upper()
    year = _current_academic_year(db, tenant_id, school_id)
    if year is None:
        return None
    return db.scalars(
        select(Section)
        .join(Grade, Section.grade_id == Grade.id)
        .where(
            Section.tenant_id == tenant_id,
            Section.academic_year_id == year.id,
            Grade.school_id == school_id,
            Grade.level == level,
            Section.name == name,
        )
    ).first()


def _resolve_term_sequence(
    db: Session,
    tenant_id: uuid.UUID,
    school_id: int,
    term_sequence: int,
) -> Term | None:
    year = _current_academic_year(db, tenant_id, school_id)
    if year is None:
        return None
    return db.scalars(
        select(Term).where(
            Term.tenant_id == tenant_id,
            Term.academic_year_id == year.id,
            Term.sequence == term_sequence,
        )
    ).first()


def _teacher_for_user(db: Session, tenant_id: uuid.UUID, user: User) -> Teacher | None:
    return db.scalars(
        select(Teacher).where(Teacher.tenant_id == tenant_id, Teacher.user_id == user.id)
    ).first()


def _ensure_teacher(
    db: Session, tenant_id: uuid.UUID, user: User, employee_code: str
) -> Teacher:
    teacher = _teacher_for_user(db, tenant_id, user)
    if teacher is not None:
        return teacher
    teacher = Teacher(
        tenant_id=tenant_id,
        user_id=user.id,
        employee_code=employee_code[:32],
    )
    db.add(teacher)
    db.flush()
    return teacher


def _apply_school_structure(db: Session, tenant_id: uuid.UUID, path: Path) -> int:
    from app.import_.models import SchoolStructureRow

    school = db.scalars(select(School).where(School.tenant_id == tenant_id)).first()
    if school is None:
        return 0
    year = _current_academic_year(db, tenant_id, school.id)
    if year is None:
        return 0

    count = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            data = SchoolStructureRow.model_validate(row)
            grade = db.scalars(
                select(Grade).where(
                    Grade.school_id == school.id, Grade.level == data.grade_level
                )
            ).first()
            if grade is None:
                grade = Grade(
                    tenant_id=tenant_id,
                    school_id=school.id,
                    name=data.grade_name,
                    level=data.grade_level,
                )
                db.add(grade)
                db.flush()
                count += 1
            elif grade.name != data.grade_name:
                grade.name = data.grade_name

            subject = db.scalars(
                select(Subject).where(
                    Subject.school_id == school.id, Subject.code == data.subject_code
                )
            ).first()
            if subject is None:
                subject = Subject(
                    tenant_id=tenant_id,
                    school_id=school.id,
                    code=data.subject_code,
                    name=data.subject_name,
                )
                db.add(subject)
                db.flush()
                count += 1
            elif subject.name != data.subject_name:
                subject.name = data.subject_name

            section = db.scalars(
                select(Section).where(
                    Section.grade_id == grade.id,
                    Section.academic_year_id == year.id,
                    Section.name == data.section_name,
                )
            ).first()
            if section is None:
                db.add(
                    Section(
                        tenant_id=tenant_id,
                        grade_id=grade.id,
                        academic_year_id=year.id,
                        name=data.section_name,
                    )
                )
                count += 1
    return count


def _apply_users(
    db: Session, tenant_id: uuid.UUID, path: Path
) -> UsersApplyResult:
    from app.import_.models import UserRow

    school = _tenant_school(db, tenant_id)
    count = 0
    warnings: list[ApplyWarning] = []
    created_users: list[CreatedUserCredential] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            data = UserRow.model_validate(row)
            csv_role = Role(data.role)
            existing = db.scalars(
                select(User).where(
                    User.tenant_id == tenant_id, User.email == data.email
                )
            ).first()
            if existing is None:
                temporary_password = secrets.token_urlsafe(16)
                user = User(
                    tenant_id=tenant_id,
                    email=data.email,
                    full_name=data.full_name,
                    role=csv_role,
                    password_hash=hash_password(temporary_password),
                    external_id=data.external_id,
                    source_system=data.source_system,
                )
                db.add(user)
                db.flush()
                created_users.append(
                    {"email": data.email, "temporary_password": temporary_password}
                )
                count += 1
            else:
                user = existing
                existing.full_name = data.full_name
                existing.external_id = data.external_id
                existing.source_system = data.source_system
                if existing.role != csv_role:
                    warnings.append(
                        _row_warning(
                            path.name,
                            row_number,
                            (
                                f"User role {existing.role.value!r} does not match "
                                f"CSV role {csv_role.value!r}; skipping role change "
                                "and portal links"
                            ),
                        )
                    )

            if user.role != csv_role:
                continue

            if csv_role is Role.STUDENT and data.admission_no:
                student = db.scalars(
                    select(Student).where(
                        Student.tenant_id == tenant_id,
                        Student.admission_no == data.admission_no,
                    )
                ).first()
                if student is None:
                    if school is None:
                        warnings.append(
                            _row_warning(
                                path.name,
                                row_number,
                                f"Student admission_no {data.admission_no!r} not found",
                            )
                        )
                    else:
                        student, error = _ensure_student(
                            db,
                            tenant_id,
                            school,
                            user=user,
                            admission_no=data.admission_no,
                            full_name=data.full_name,
                            section_code=data.section_code,
                            external_id=data.external_id,
                            source_system=data.source_system,
                        )
                        if error:
                            warnings.append(
                                _row_warning(path.name, row_number, error)
                            )
                else:
                    student.user_id = user.id
            elif csv_role is Role.PARENT and data.admission_no:
                student = db.scalars(
                    select(Student).where(
                        Student.tenant_id == tenant_id,
                        Student.admission_no == data.admission_no,
                    )
                ).first()
                if student is None:
                    warnings.append(
                        _row_warning(
                            path.name,
                            row_number,
                            f"Student admission_no {data.admission_no!r} not found for parent link",
                        )
                    )
                else:
                    student.guardian_user_id = user.id
            elif csv_role is Role.TEACHER and school is not None:
                employee_code = (
                    data.external_id
                    or data.email.split("@", maxsplit=1)[0]
                )[:32]
                teacher = _ensure_teacher(db, tenant_id, user, employee_code)
                if data.section_code and data.subject_codes:
                    section = _resolve_section_code(
                        db, tenant_id, school.id, data.section_code
                    )
                    if section is None:
                        warnings.append(
                            _row_warning(
                                path.name,
                                row_number,
                                f"section_code {data.section_code!r} not found",
                            )
                        )
                    else:
                        year = _current_academic_year(db, tenant_id, school.id)
                        for code in _parse_subject_codes(data.subject_codes):
                            subject = db.scalars(
                                select(Subject).where(
                                    Subject.school_id == school.id,
                                    Subject.code == code,
                                )
                            ).first()
                            if subject is None:
                                warnings.append(
                                    _row_warning(
                                        path.name,
                                        row_number,
                                        f"subject_code {code!r} not found",
                                    )
                                )
                                continue
                            if year is None:
                                continue
                            assignment = db.scalars(
                                select(TeacherAssignment).where(
                                    TeacherAssignment.subject_id == subject.id,
                                    TeacherAssignment.section_id == section.id,
                                    TeacherAssignment.academic_year_id == year.id,
                                )
                            ).first()
                            if assignment is None:
                                db.add(
                                    TeacherAssignment(
                                        tenant_id=tenant_id,
                                        teacher_id=teacher.id,
                                        subject_id=subject.id,
                                        section_id=section.id,
                                        academic_year_id=year.id,
                                    )
                                )
                            elif assignment.teacher_id != teacher.id:
                                assignment.teacher_id = teacher.id
    return count, warnings, created_users


def _apply_scores(db: Session, tenant_id: uuid.UUID, path: Path) -> ApplyResult:
    from app.import_.models import ScoreRow

    school = _tenant_school(db, tenant_id)
    count = 0
    warnings: list[ApplyWarning] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            data = ScoreRow.model_validate(row)
            student = db.scalars(
                select(Student).where(
                    Student.tenant_id == tenant_id,
                    Student.admission_no == data.admission_no,
                )
            ).first()
            if student is None:
                warnings.append(
                    _row_warning(
                        path.name,
                        row_number,
                        f"Student admission_no {data.admission_no!r} not found",
                    )
                )
                continue
            if school is None:
                warnings.append(
                    _row_warning(
                        path.name,
                        row_number,
                        "School not provisioned for assessment import",
                    )
                )
                continue
            assessment, error = _ensure_assessment(
                db,
                tenant_id,
                school,
                assessment_code=data.assessment_code,
                student=student,
                source_system=data.source_system,
            )
            if assessment is None:
                warnings.append(
                    _row_warning(
                        path.name,
                        row_number,
                        error or f"Assessment code {data.assessment_code!r} not found",
                    )
                )
                continue
            if student.section_id != assessment.section_id:
                warnings.append(
                    _row_warning(
                        path.name,
                        row_number,
                        f"Student {data.admission_no!r} is not in the assessment section",
                    )
                )
                continue
            existing = db.scalars(
                select(Score).where(
                    Score.assessment_id == assessment.id,
                    Score.student_id == student.id,
                )
            ).first()
            if existing is None:
                db.add(
                    Score(
                        tenant_id=tenant_id,
                        assessment_id=assessment.id,
                        student_id=student.id,
                        marks_obtained=data.marks_obtained,
                        is_absent=data.is_absent,
                        external_id=data.external_id,
                        source_system=data.source_system,
                    )
                )
                count += 1
            else:
                existing.marks_obtained = data.marks_obtained
                existing.is_absent = data.is_absent
                existing.external_id = data.external_id
                existing.source_system = data.source_system
                count += 1
    return count, warnings


def _apply_attendance(db: Session, tenant_id: uuid.UUID, path: Path) -> ApplyResult:
    from app.import_.models import AttendanceRow

    count = 0
    warnings: list[ApplyWarning] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            data = AttendanceRow.model_validate(row)
            student = db.scalars(
                select(Student).where(
                    Student.tenant_id == tenant_id,
                    Student.admission_no == data.admission_no,
                )
            ).first()
            if student is None:
                warnings.append(
                    _row_warning(
                        path.name,
                        row_number,
                        f"Student admission_no {data.admission_no!r} not found",
                    )
                )
                continue
            existing = db.scalars(
                select(Attendance).where(
                    Attendance.student_id == student.id,
                    Attendance.on_date == data.on_date,
                )
            ).first()
            if existing is None:
                db.add(
                    Attendance(
                        tenant_id=tenant_id,
                        student_id=student.id,
                        on_date=data.on_date,
                        status=AttendanceStatus(data.status),
                        external_id=data.external_id,
                        source_system=data.source_system,
                    )
                )
                count += 1
            else:
                existing.status = AttendanceStatus(data.status)
                existing.external_id = data.external_id
                existing.source_system = data.source_system
                count += 1
    return count, warnings


def _apply_remarks(db: Session, tenant_id: uuid.UUID, path: Path) -> ApplyResult:
    from app.import_.models import RemarkRow

    school = _tenant_school(db, tenant_id)
    count = 0
    warnings: list[ApplyWarning] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            data = RemarkRow.model_validate(row)
            student = db.scalars(
                select(Student).where(
                    Student.tenant_id == tenant_id,
                    Student.admission_no == data.admission_no,
                )
            ).first()
            if student is None:
                warnings.append(
                    _row_warning(
                        path.name,
                        row_number,
                        f"Student admission_no {data.admission_no!r} not found",
                    )
                )
                continue

            teacher_id: int | None = None
            if data.teacher_email is not None:
                teacher_user = db.scalars(
                    select(User).where(
                        User.tenant_id == tenant_id,
                        User.email == data.teacher_email,
                    )
                ).first()
                if teacher_user is None:
                    warnings.append(
                        _row_warning(
                            path.name,
                            row_number,
                            f"Teacher email {data.teacher_email!r} not found",
                        )
                    )
                else:
                    teacher = _teacher_for_user(db, tenant_id, teacher_user)
                    if teacher is None:
                        warnings.append(
                            _row_warning(
                                path.name,
                                row_number,
                                f"Teacher profile for {data.teacher_email!r} not found",
                            )
                        )
                    else:
                        teacher_id = teacher.id

            term_id: int | None = None
            if data.term_sequence is not None and school is not None:
                term = _resolve_term_sequence(
                    db, tenant_id, school.id, data.term_sequence
                )
                if term is None:
                    warnings.append(
                        _row_warning(
                            path.name,
                            row_number,
                            f"term_sequence {data.term_sequence} not found",
                        )
                    )
                else:
                    term_id = term.id

            subject_id: int | None = None
            if data.subject_code and school is not None:
                subject = db.scalars(
                    select(Subject).where(
                        Subject.school_id == school.id,
                        Subject.code == data.subject_code,
                    )
                ).first()
                if subject is None:
                    warnings.append(
                        _row_warning(
                            path.name,
                            row_number,
                            f"subject_code {data.subject_code!r} not found",
                        )
                    )
                else:
                    subject_id = subject.id

            if data.external_id:
                existing = db.scalars(
                    select(Remark).where(
                        Remark.tenant_id == tenant_id,
                        Remark.external_id == data.external_id,
                    )
                ).first()
                if existing is not None:
                    if existing.student_id != student.id:
                        warnings.append(
                            _row_warning(
                                path.name,
                                row_number,
                                (
                                    f"external_id {data.external_id!r} belongs to a "
                                    "different student; skipping row"
                                ),
                            )
                        )
                        continue
                    else:
                        existing.category = RemarkCategory(data.category)
                        existing.body = data.body
                        existing.source_system = data.source_system
                        if teacher_id is not None:
                            existing.teacher_id = teacher_id
                        if term_id is not None:
                            existing.term_id = term_id
                        if subject_id is not None:
                            existing.subject_id = subject_id
                        count += 1
                        continue
            else:
                natural_key = [
                    Remark.tenant_id == tenant_id,
                    Remark.student_id == student.id,
                    Remark.category == RemarkCategory(data.category),
                    Remark.body == data.body,
                ]
                if term_id is None:
                    natural_key.append(Remark.term_id.is_(None))
                else:
                    natural_key.append(Remark.term_id == term_id)
                if subject_id is None:
                    natural_key.append(Remark.subject_id.is_(None))
                else:
                    natural_key.append(Remark.subject_id == subject_id)
                existing = db.scalars(select(Remark).where(*natural_key)).first()
                if existing is not None:
                    if teacher_id is not None:
                        existing.teacher_id = teacher_id
                    if data.source_system is not None:
                        existing.source_system = data.source_system
                    count += 1
                    continue
            db.add(
                Remark(
                    tenant_id=tenant_id,
                    student_id=student.id,
                    teacher_id=teacher_id,
                    term_id=term_id,
                    subject_id=subject_id,
                    category=RemarkCategory(data.category),
                    body=data.body,
                    external_id=data.external_id,
                    source_system=data.source_system,
                )
            )
            count += 1
    return count, warnings


def apply_uploads(
    db: Session,
    tenant_id: uuid.UUID,
    files: list[UploadFile],
    *,
    user_id: int | None = None,
) -> dict[str, object]:
    school = db.scalars(select(School).where(School.tenant_id == tenant_id)).first()
    if school is None:
        return {"valid": False, "applied": False, "errors": [{"error": "School not provisioned"}]}

    with tempfile.TemporaryDirectory(prefix="school-import-") as tmp:
        directory = Path(tmp)
        extract_errors = write_named_uploads(files, directory)
        if extract_errors:
            from app.import_.history import record_import_run
            from app.models import ImportRunStatus

            report = {
                "valid": False,
                "applied": False,
                "errors": [{"error": err} for err in extract_errors],
            }
            record_import_run(
                db,
                tenant_id=tenant_id,
                status=ImportRunStatus.FAILED,
                source="upload",
                report=report,
                user_id=user_id,
            )
            db.commit()
            return report
        return apply_directory(
            db, tenant_id, directory, source="upload", user_id=user_id
        )
