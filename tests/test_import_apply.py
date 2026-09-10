"""Import apply pipeline tests."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import select

from app.import_.apply import apply_directory
from app.import_.validate import validate_directory
from app.models import (
    AcademicYear,
    Assessment,
    AssessmentType,
    Attendance,
    AttendanceStatus,
    Grade,
    Remark,
    Role,
    Score,
    Section,
    Student,
    Subject,
    Teacher,
    TeacherAssignment,
    Tenant,
    Term,
    User,
)


def _write_csv(path, header: str, row: str) -> None:
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")


@pytest.fixture
def tenant_bundle_dir(tmp_path, db):
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    return tenant.id, tmp_path


class TestApplyScores:
    def test_apply_scores_creates_assessment_from_code(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(
            select(Student)
            .join(Section, Student.section_id == Section.id)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "A")
            .limit(1)
        ).one()

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},UT-MATH-6A-T1,38,false,score-new,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["scores.csv"] == 1
        assert report.get("warnings", []) == []

        assessment = db.scalars(
            select(Assessment).where(
                Assessment.tenant_id == tenant_id,
                Assessment.external_id == "UT-MATH-6A-T1",
            )
        ).one()
        assert assessment.section_id == student.section_id
        score = db.scalars(
            select(Score).where(
                Score.student_id == student.id, Score.assessment_id == assessment.id
            )
        ).one()
        assert score.marks_obtained == 38.0

    def test_apply_scores_inserts_and_updates(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()
        section = db.get(Section, student.section_id)
        subject = db.scalars(select(Subject).limit(1)).one()
        year = db.get(AcademicYear, section.academic_year_id)
        term = db.scalars(
            select(Term).where(Term.academic_year_id == year.id).limit(1)
        ).one()
        assessment = Assessment(
            tenant_id=tenant_id,
            name="Import test assessment",
            assessment_type=AssessmentType.UNIT_TEST,
            subject_id=subject.id,
            section_id=section.id,
            term_id=term.id,
            max_marks=50,
            conducted_on=date(2025, 8, 1),
            external_id="IMPORT-UT-6A-T1",
        )
        db.add(assessment)
        db.commit()

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},IMPORT-UT-6A-T1,42,false,score-1,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["scores.csv"] == 1

        score = db.scalars(
            select(Score).where(
                Score.student_id == student.id, Score.assessment_id == assessment.id
            )
        ).one()
        assert score.marks_obtained == 42.0

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},IMPORT-UT-6A-T1,45,false,score-1,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        db.refresh(score)
        assert report["rows"]["scores.csv"] == 1
        assert score.marks_obtained == 45.0


class TestApplyAttendance:
    def test_apply_attendance_inserts_and_updates(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()
        on_date = date(2025, 8, 15)

        _write_csv(
            directory / "attendance.csv",
            "admission_no,on_date,status,external_id,source_system",
            f"{student.admission_no},{on_date.isoformat()},present,att-1,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["attendance.csv"] == 1

        row = db.scalars(
            select(Attendance).where(
                Attendance.student_id == student.id,
                Attendance.on_date == on_date,
            )
        ).one()
        assert row.status == AttendanceStatus.PRESENT
        assert row.external_id == "att-1"

        _write_csv(
            directory / "attendance.csv",
            "admission_no,on_date,status,external_id,source_system",
            f"{student.admission_no},{on_date.isoformat()},absent,att-1,erp-v2",
        )
        report = apply_directory(db, tenant_id, directory)
        db.refresh(row)
        assert report["rows"]["attendance.csv"] == 1
        assert row.status == AttendanceStatus.ABSENT
        assert row.source_system == "erp-v2"

    def test_apply_attendance_reports_unknown_admission_no(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()
        on_date = date(2025, 8, 20)

        _write_csv(
            directory / "attendance.csv",
            "admission_no,on_date,status,external_id,source_system",
            f"{student.admission_no},{on_date.isoformat()},present,att-ok,erp\n"
            f"MISSING-ADM,{on_date.isoformat()},present,att-skip,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        assert report["rows"]["attendance.csv"] == 1
        warnings = report.get("warnings", [])
        assert any("MISSING-ADM" in item["error"] for item in warnings)

        assert db.scalars(
            select(Attendance).where(Attendance.external_id == "att-ok")
        ).one()
        assert (
            db.scalars(select(Attendance).where(Attendance.external_id == "att-skip")).first()
            is None
        )


class TestApplyRemarks:
    def test_apply_remarks_upserts_by_external_id(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()
        teacher = db.scalars(select(Teacher).where(Teacher.tenant_id == tenant_id).limit(1)).one()
        teacher_email = db.get(User, teacher.user_id).email

        header = (
            "admission_no,teacher_email,category,body,term_sequence,subject_code,"
            "external_id,source_system"
        )
        _write_csv(
            directory / "remarks.csv",
            header,
            f"{student.admission_no},{teacher_email},concern,First note,1,MATH,rmk-1,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["remarks.csv"] == 1
        assert report.get("warnings", []) == []

        remark = db.scalars(
            select(Remark).where(
                Remark.tenant_id == tenant_id,
                Remark.external_id == "rmk-1",
            )
        ).one()
        assert remark.body == "First note"
        assert remark.category.value == "concern"
        assert remark.teacher_id == teacher.id
        assert remark.term_id is not None
        assert remark.subject_id is not None

        _write_csv(
            directory / "remarks.csv",
            header,
            f"{student.admission_no},{teacher_email},academic,Updated note,1,MATH,rmk-1,erp-v2",
        )
        report = apply_directory(db, tenant_id, directory)
        db.refresh(remark)
        assert report["rows"]["remarks.csv"] == 1
        assert db.scalars(select(Remark).where(Remark.external_id == "rmk-1")).one().id == remark.id
        assert remark.body == "Updated note"
        assert remark.category.value == "academic"
        assert remark.source_system == "erp-v2"

    def test_apply_remarks_upsert_preserves_optional_associations(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()
        teacher = db.scalars(select(Teacher).where(Teacher.tenant_id == tenant_id).limit(1)).one()
        teacher_email = db.get(User, teacher.user_id).email

        full_header = (
            "admission_no,teacher_email,category,body,term_sequence,subject_code,"
            "external_id,source_system"
        )
        _write_csv(
            directory / "remarks.csv",
            full_header,
            f"{student.admission_no},{teacher_email},concern,First note,1,MATH,rmk-preserve,erp",
        )
        apply_directory(db, tenant_id, directory)

        remark = db.scalars(
            select(Remark).where(
                Remark.tenant_id == tenant_id,
                Remark.external_id == "rmk-preserve",
            )
        ).one()
        assert remark.teacher_id == teacher.id
        assert remark.term_id is not None
        assert remark.subject_id is not None
        original_teacher_id = remark.teacher_id
        original_term_id = remark.term_id
        original_subject_id = remark.subject_id

        minimal_header = "admission_no,category,body,external_id,source_system"
        _write_csv(
            directory / "remarks.csv",
            minimal_header,
            f"{student.admission_no},academic,Updated note,rmk-preserve,erp-v2",
        )
        report = apply_directory(db, tenant_id, directory)
        db.refresh(remark)

        assert report["rows"]["remarks.csv"] == 1
        assert remark.body == "Updated note"
        assert remark.category.value == "academic"
        assert remark.source_system == "erp-v2"
        assert remark.teacher_id == original_teacher_id
        assert remark.term_id == original_term_id
        assert remark.subject_id == original_subject_id

    def test_apply_remarks_skips_duplicates_without_external_id(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()
        teacher = db.scalars(select(Teacher).where(Teacher.tenant_id == tenant_id).limit(1)).one()
        teacher_email = db.get(User, teacher.user_id).email

        header = "admission_no,teacher_email,category,body,term_sequence,subject_code,source_system"
        row = f"{student.admission_no},{teacher_email},concern,Needs follow-up,1,MATH,erp"
        _write_csv(directory / "remarks.csv", header, row)

        first_report = apply_directory(db, tenant_id, directory)
        assert first_report["rows"]["remarks.csv"] == 1
        remark_count = len(
            db.scalars(
                select(Remark).where(
                    Remark.tenant_id == tenant_id,
                    Remark.student_id == student.id,
                    Remark.body == "Needs follow-up",
                )
            ).all()
        )
        assert remark_count == 1

        second_report = apply_directory(db, tenant_id, directory)
        assert second_report["rows"]["remarks.csv"] == 1
        assert len(
            db.scalars(
                select(Remark).where(
                    Remark.tenant_id == tenant_id,
                    Remark.student_id == student.id,
                    Remark.body == "Needs follow-up",
                )
            ).all()
        ) == 1

    def test_apply_remarks_skips_upsert_on_student_mismatch(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        students = db.scalars(
            select(Student)
            .where(Student.tenant_id == tenant_id)
            .order_by(Student.id.desc())
            .limit(2)
        ).all()
        assert len(students) == 2
        first, second = students
        teacher = db.scalars(select(Teacher).where(Teacher.tenant_id == tenant_id).limit(1)).one()
        teacher_email = db.get(User, teacher.user_id).email
        header = (
            "admission_no,teacher_email,category,body,term_sequence,subject_code,"
            "external_id,source_system"
        )

        _write_csv(
            directory / "remarks.csv",
            header,
            f"{first.admission_no},{teacher_email},concern,Original note,1,MATH,rmk-mismatch,erp",
        )
        apply_directory(db, tenant_id, directory)

        _write_csv(
            directory / "remarks.csv",
            header,
            f"{second.admission_no},{teacher_email},academic,Different student,1,MATH,rmk-mismatch,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        assert any("different student" in item["error"] for item in report.get("warnings", []))

        remarks = db.scalars(
            select(Remark).where(Remark.tenant_id == tenant_id, Remark.external_id == "rmk-mismatch")
        ).all()
        assert len(remarks) == 1
        assert remarks[0].student_id == first.id
        assert remarks[0].body == "Original note"


class TestApplyScoresSectionResolution:
    def test_apply_scores_skips_when_assessment_section_unresolved(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(
            select(Student)
            .join(Section, Student.section_id == Section.id)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "A")
            .limit(1)
        ).one()

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},UT-MATH-6Z-T1,55,false,score-bad-section,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        assert report["rows"]["scores.csv"] == 0
        warnings = report.get("warnings", [])
        assert any("section for assessment code" in item["error"] for item in warnings)

        assessment = db.scalars(
            select(Assessment).where(
                Assessment.tenant_id == tenant_id,
                Assessment.external_id == "UT-MATH-6Z-T1",
            )
        ).first()
        assert assessment is None
        score = db.scalars(
            select(Score).where(Score.student_id == student.id, Score.external_id == "score-bad-section")
        ).first()
        assert score is None

    def test_apply_scores_skips_when_student_section_mismatch(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(
            select(Student)
            .join(Section, Student.section_id == Section.id)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "A")
            .limit(1)
        ).one()
        other_section = db.scalars(
            select(Section)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "B")
            .limit(1)
        ).one()
        subject = db.scalars(select(Subject).limit(1)).one()
        term = db.scalars(
            select(Term).where(Term.academic_year_id == other_section.academic_year_id).limit(1)
        ).one()
        assessment = Assessment(
            tenant_id=tenant_id,
            name="Section B unit test",
            assessment_type=AssessmentType.UNIT_TEST,
            subject_id=subject.id,
            section_id=other_section.id,
            term_id=term.id,
            max_marks=100.0,
            conducted_on=date(2025, 8, 2),
            external_id="UT-MATH-6B-T1",
        )
        db.add(assessment)
        db.commit()

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},UT-MATH-6B-T1,55,false,score-wrong-section,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        assert report["rows"]["scores.csv"] == 0
        warnings = report.get("warnings", [])
        assert any("not in the assessment section" in item["error"] for item in warnings)

        score = db.scalars(
            select(Score).where(
                Score.student_id == student.id,
                Score.external_id == "score-wrong-section",
            )
        ).first()
        assert score is None


class TestApplyUsers:
    def test_apply_users_creates_student_when_missing(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        section = db.scalars(
            select(Section)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "A")
            .limit(1)
        ).one()
        grade = db.get(Grade, section.grade_id)
        section_code = f"{grade.level}{section.name}"

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            f"new.student@example.com,New Student,student,NEW-ADM-9001,{section_code},,imp-new,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["users.csv"] == 1
        assert report.get("warnings", []) == []

        student = db.scalars(
            select(Student).where(
                Student.tenant_id == tenant_id,
                Student.admission_no == "NEW-ADM-9001",
            )
        ).one()
        assert student.full_name == "New Student"
        assert student.section_id == section.id
        user = db.scalars(
            select(User).where(
                User.tenant_id == tenant_id, User.email == "new.student@example.com"
            )
        ).one()
        assert student.user_id == user.id

    def test_apply_users_links_student_by_admission_no(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()
        student.user_id = None
        db.commit()

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            f"import.student@example.com,Import Student,student,{student.admission_no},,,imp-stu,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["users.csv"] == 1
        db.refresh(student)
        user = db.scalars(
            select(User).where(User.tenant_id == tenant_id, User.email == "import.student@example.com")
        ).one()
        assert student.user_id == user.id

    def test_apply_users_links_teacher_assignments(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        teacher = db.scalars(select(Teacher).where(Teacher.tenant_id == tenant_id).limit(1)).one()
        assignment = db.scalars(
            select(TeacherAssignment).where(TeacherAssignment.teacher_id == teacher.id).limit(1)
        ).one()
        section = db.get(Section, assignment.section_id)
        grade = db.get(Grade, section.grade_id)
        subject = db.get(Subject, assignment.subject_id)
        section_code = f"{grade.level}{section.name}"

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            f"import.teacher@example.com,Import Teacher,teacher,,{section_code},{subject.code},imp-tch,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["users.csv"] == 1

        user = db.scalars(
            select(User).where(User.tenant_id == tenant_id, User.email == "import.teacher@example.com")
        ).one()
        imported_teacher = db.scalars(select(Teacher).where(Teacher.user_id == user.id)).one()
        linked = db.scalars(
            select(TeacherAssignment).where(
                TeacherAssignment.teacher_id == imported_teacher.id,
                TeacherAssignment.section_id == section.id,
                TeacherAssignment.subject_id == subject.id,
            )
        ).one()
        assert linked.academic_year_id == section.academic_year_id

    def test_apply_users_skips_parent_link_when_email_is_student_role(
        self, db, tenant_bundle_dir
    ):
        tenant_id, directory = tenant_bundle_dir
        student_user = db.scalars(
            select(User).where(User.tenant_id == tenant_id, User.role == Role.STUDENT).limit(1)
        ).one()
        target = db.scalars(
            select(Student).where(Student.tenant_id == tenant_id).limit(1)
        ).one()
        original_guardian_id = target.guardian_user_id
        target.guardian_user_id = None
        db.commit()

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            f"{student_user.email},Wrong Parent,parent,{target.admission_no},,,imp-bad-parent,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        warnings = report.get("warnings", [])
        assert any("does not match CSV role 'parent'" in item["error"] for item in warnings)

        db.refresh(target)
        assert target.guardian_user_id is None
        assert student_user.role is Role.STUDENT

        target.guardian_user_id = original_guardian_id
        db.commit()

    def test_apply_users_skips_student_link_when_email_is_parent_role(
        self, db, tenant_bundle_dir
    ):
        tenant_id, directory = tenant_bundle_dir
        parent_user = db.scalars(
            select(User).where(User.tenant_id == tenant_id, User.role == Role.PARENT).limit(1)
        ).one()
        target = db.scalars(
            select(Student).where(Student.tenant_id == tenant_id).limit(1)
        ).one()
        original_user_id = target.user_id
        target.user_id = None
        db.commit()

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            f"{parent_user.email},Wrong Student,student,{target.admission_no},,,imp-bad-student,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        warnings = report.get("warnings", [])
        assert any("does not match CSV role 'student'" in item["error"] for item in warnings)

        db.refresh(target)
        assert target.user_id is None
        assert parent_user.role is Role.PARENT

        target.user_id = original_user_id
        db.commit()

    def test_apply_users_unique_password_hashes_per_user(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        section = db.scalars(
            select(Section)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "A")
            .limit(1)
        ).one()
        grade = db.get(Grade, section.grade_id)
        section_code = f"{grade.level}{section.name}"

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            (
                f"import.user.a@example.com,Import User A,student,NEW-ADM-A,{section_code},,imp-a,erp\n"
                f"import.user.b@example.com,Import User B,student,NEW-ADM-B,{section_code},,imp-b,erp"
            ),
        )
        report = apply_directory(db, tenant_id, directory, source="cli")
        assert report["applied"] is True
        assert report["rows"]["users.csv"] == 2

        users = db.scalars(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email.in_(
                    ["import.user.a@example.com", "import.user.b@example.com"]
                ),
            )
        ).all()
        assert len(users) == 2
        assert users[0].password_hash != users[1].password_hash

        created = report.get("created_users", [])
        assert len(created) == 2
        emails = {item["email"] for item in created}
        assert emails == {
            "import.user.a@example.com",
            "import.user.b@example.com",
        }
        passwords = {item["temporary_password"] for item in created}
        assert len(passwords) == 2

    def test_apply_users_excludes_credentials_for_webhook_source(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        section = db.scalars(
            select(Section)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "A")
            .limit(1)
        ).one()
        grade = db.get(Grade, section.grade_id)
        section_code = f"{grade.level}{section.name}"

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            f"webhook.user@example.com,Webhook User,student,NEW-ADM-WH,{section_code},,imp-wh,erp",
        )
        report = apply_directory(db, tenant_id, directory, source="webhook")
        assert report["applied"] is True
        assert "created_users" not in report

    def test_apply_users_links_parent_guardian_happy_path(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        child = db.scalars(
            select(Student)
            .where(Student.tenant_id == tenant_id, Student.guardian_user_id.is_not(None))
            .limit(1)
        ).one()
        guardian = db.get(User, child.guardian_user_id)
        original_guardian_id = child.guardian_user_id
        child.guardian_user_id = None
        db.commit()

        _write_csv(
            directory / "users.csv",
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system",
            f"{guardian.email},{guardian.full_name},parent,{child.admission_no},,,imp-good-parent,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("warnings", []) == []

        db.refresh(child)
        assert child.guardian_user_id == guardian.id

        child.guardian_user_id = original_guardian_id
        db.commit()


class TestApplyScoresWarnings:
    def test_apply_scores_reports_skipped_rows(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = db.scalars(select(Student).where(Student.tenant_id == tenant_id).limit(1)).one()

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            "MISSING-ADM,UT-MATH-6A-T1,10,false,skip-1,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        assert report["rows"]["scores.csv"] == 0
        warnings = report.get("warnings", [])
        assert any("MISSING-ADM" in item["error"] for item in warnings)

        section = db.get(Section, student.section_id)
        subject = db.scalars(select(Subject).limit(1)).one()
        year = db.get(AcademicYear, section.academic_year_id)
        term = db.scalars(
            select(Term).where(Term.academic_year_id == year.id).limit(1)
        ).one()
        assessment = Assessment(
            tenant_id=tenant_id,
            name="Partial apply test",
            assessment_type=AssessmentType.UNIT_TEST,
            subject_id=subject.id,
            section_id=section.id,
            term_id=term.id,
            max_marks=50,
            conducted_on=date(2025, 8, 2),
            external_id="UT-PARTIAL-T1",
        )
        db.add(assessment)
        db.commit()

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},UT-PARTIAL-T1,20,false,ok-1,erp\n"
            f"MISSING-ADM,UT-PARTIAL-T1,10,false,skip-2,erp",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report.get("partial") is True
        assert report["rows"]["scores.csv"] == 1
        assert len(report.get("warnings", [])) == 1


class TestApplySchoolStructure:
    def test_apply_school_structure_creates_entities(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        _write_csv(
            directory / "school_structure.csv",
            "grade_level,grade_name,section_name,subject_code,subject_name",
            "9,Grade 9,Z,ART,Art and Design",
        )
        report = apply_directory(db, tenant_id, directory)
        assert report["applied"] is True
        assert report["rows"]["school_structure.csv"] >= 1

        grade = db.scalars(
            select(Grade).where(Grade.tenant_id == tenant_id, Grade.level == 9)
        ).one()
        assert grade.name == "Grade 9"
        subject = db.scalars(
            select(Subject).where(Subject.tenant_id == tenant_id, Subject.code == "ART")
        ).one()
        assert subject.name == "Art and Design"
        section = db.scalars(
            select(Section).where(Section.grade_id == grade.id, Section.name == "Z")
        ).one()
        assert section.tenant_id == tenant_id


def _enable_bulk_import(db, *, tenant_key: str = "sunrise") -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models import Tenant

    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    features["bulkImport"] = True
    settings["features"] = features
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


def _set_erp_webhook_token(db, token: str, *, tenant_key: str = "sunrise") -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models import Tenant

    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    erp = dict(settings.get("erp") or {})
    erp["webhookToken"] = token
    settings["erp"] = erp
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


class TestImportApiGuards:
    def test_admin_apply_includes_created_user_credentials(self, admin_client, db):
        from app.models import ImportRun, Tenant

        _enable_bulk_import(db)
        section = db.scalars(
            select(Section)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Grade.level == 6, Section.name == "A")
            .limit(1)
        ).one()
        grade = db.get(Grade, section.grade_id)
        section_code = f"{grade.level}{section.name}"
        users_csv = (
            "email,full_name,role,admission_no,section_code,subject_codes,external_id,source_system\n"
            f"api.import.user@example.com,API Import User,student,NEW-ADM-API,{section_code},,imp-api,erp\n"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("users.csv", users_csv)
        buffer.seek(0)

        response = admin_client.post(
            "/api/import/apply",
            files={"file": ("bundle.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["applied"] is True
        created = payload.get("created_users", [])
        assert len(created) == 1
        assert created[0]["email"] == "api.import.user@example.com"
        assert created[0]["temporary_password"]

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        run = db.scalars(
            select(ImportRun)
            .where(ImportRun.tenant_id == tenant.id)
            .order_by(ImportRun.created_at.desc())
            .limit(1)
        ).one()
        assert run.row_counts == {"users.csv": 1}

    def test_apply_skips_on_extract_error(self, admin_client, db):
        from app.models import ImportRun, Tenant

        _enable_bulk_import(db)

        before_scores = db.scalars(select(Score.id)).all()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("not-a-csv.txt", "noop")
        buffer.seek(0)
        response = admin_client.post(
            "/api/import/apply",
            files={"file": ("bundle.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["applied"] is False
        after_scores = db.scalars(select(Score.id)).all()
        assert len(after_scores) == len(before_scores)

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        run = db.scalars(
            select(ImportRun)
            .where(ImportRun.tenant_id == tenant.id)
            .order_by(ImportRun.created_at.desc())
            .limit(1)
        ).one()
        assert run.status.value == "failed"


class TestValidateDirectory:
    def test_validate_empty_dir(self, tmp_path):
        report = validate_directory(tmp_path)
        assert report["valid"] is False
        assert report["summary"] == {}
        assert any("No recognized CSV files" in err["error"] for err in report["errors"])

    def test_validate_unrecognized_files_only(self, tmp_path):
        (tmp_path / "notes.txt").write_text("not a csv bundle", encoding="utf-8")
        report = validate_directory(tmp_path)
        assert report["valid"] is False
        assert any("No recognized CSV files" in err["error"] for err in report["errors"])
