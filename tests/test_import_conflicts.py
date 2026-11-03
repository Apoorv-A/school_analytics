"""ERP import conflict detection and admin resolution."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.audit import SecurityAuditAction, SecurityAuditTarget
from app.import_.apply import apply_directory
from app.import_.conflicts import ANALYTICS_SOURCE_SYSTEM
from app.models import (
    ImportConflict,
    ImportConflictEntityType,
    ImportConflictStatus,
    Remark,
    Score,
    Section,
    SecurityAuditEvent,
    Student,
)
from tests.test_import_apply import (
    _assessment_for_student,
    _enable_bulk_import,
    _write_csv,
)
from tests.test_import_mapping_queue import _student_grade_6a


def _student_in_section(db, *, grade: int, section_name: str) -> Student:
    from app.models import Grade

    return db.scalars(
        select(Student)
        .join(Section, Student.section_id == Section.id)
        .join(Grade, Section.grade_id == Grade.id)
        .where(Grade.level == grade, Section.name == section_name)
        .limit(1)
    ).one()


@pytest.fixture
def tenant_bundle_dir(tmp_path, db):
    from app.models import Tenant

    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    return tenant.id, tmp_path


def _apply_score_row(
    directory,
    student: Student,
    *,
    assessment_code: str,
    marks: float,
    external_id: str,
    source: str,
):
    _write_csv(
        directory / "scores.csv",
        "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
        f"{student.admission_no},{assessment_code},{marks},false,{external_id},{source}",
    )


class TestImportConflictDetection:
    def test_webhook_queues_score_conflict_for_analytics_owned_row(
        self, db, tenant_bundle_dir
    ):
        tenant_id, directory = tenant_bundle_dir
        student = _student_grade_6a(db)
        _assessment_for_student(
            db, tenant_id, student, external_id="UT-MATH-6A-T1", max_marks=100.0
        )

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-6A-T1",
            marks=72,
            external_id="score-c1",
            source="erp-v1",
        )
        apply_directory(db, tenant_id, directory, source="webhook")

        score = db.scalars(
            select(Score).where(
                Score.tenant_id == tenant_id,
                Score.external_id == "score-c1",
            )
        ).one()
        score.marks_obtained = 85
        score.source_system = ANALYTICS_SOURCE_SYSTEM
        db.commit()

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-6A-T1",
            marks=90,
            external_id="score-c1",
            source="erp-v2",
        )
        report = apply_directory(db, tenant_id, directory, source="sync:rest")
        db.refresh(score)

        assert report.get("conflicts_queued") == 1
        assert score.marks_obtained == 85
        conflict = db.scalars(
            select(ImportConflict).where(
                ImportConflict.tenant_id == tenant_id,
                ImportConflict.entity_id == score.id,
            )
        ).one()
        assert conflict.status == ImportConflictStatus.PENDING
        assert conflict.entity_type == ImportConflictEntityType.SCORE
        assert "marks_obtained" in conflict.field_names

    def test_operator_upload_overwrites_without_queueing(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = _student_in_section(db, grade=6, section_name="B")
        _assessment_for_student(
            db, tenant_id, student, external_id="UT-MATH-6B-T1", max_marks=100.0
        )

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-6B-T1",
            marks=70,
            external_id="score-op",
            source="erp",
        )
        apply_directory(db, tenant_id, directory, source="upload")

        score = db.scalars(
            select(Score).where(Score.external_id == "score-op")
        ).one()
        score.marks_obtained = 80
        score.source_system = ANALYTICS_SOURCE_SYSTEM
        db.commit()

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-6B-T1",
            marks=95,
            external_id="score-op",
            source="erp-new",
        )
        report = apply_directory(db, tenant_id, directory, source="upload")
        db.refresh(score)

        assert report.get("conflicts_queued") is None
        assert score.marks_obtained == 95
        assert (
            db.scalars(
                select(ImportConflict).where(
                    ImportConflict.tenant_id == tenant_id,
                    ImportConflict.entity_id == score.id,
                )
            ).first()
            is None
        )

    def test_remark_conflict_via_teacher_portal_source(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = _student_grade_6a(db)

        _write_csv(
            directory / "remarks.csv",
            "admission_no,category,body,external_id,source_system",
            f"{student.admission_no},academic,ERP note,rmk-c1,erp-v1",
        )
        apply_directory(db, tenant_id, directory, source="webhook")

        remark = db.scalars(
            select(Remark).where(Remark.external_id == "rmk-c1")
        ).one()
        remark.body = "Teacher edited in portal"
        remark.source_system = ANALYTICS_SOURCE_SYSTEM
        db.commit()

        _write_csv(
            directory / "remarks.csv",
            "admission_no,category,body,external_id,source_system",
            f"{student.admission_no},academic,ERP replacement,rmk-c1,erp-v2",
        )
        report = apply_directory(db, tenant_id, directory, source="webhook")
        db.refresh(remark)

        assert report.get("conflicts_queued") == 1
        assert remark.body == "Teacher edited in portal"
        conflict = db.scalars(select(ImportConflict).where(ImportConflict.external_id == "rmk-c1")).one()
        assert conflict.entity_type == ImportConflictEntityType.REMARK


class TestImportConflictApi:
    def test_accept_erp_resolution(self, db, tenant_bundle_dir, admin_client):
        tenant_id, directory = tenant_bundle_dir
        student = _student_in_section(db, grade=7, section_name="B")
        _enable_bulk_import(db)
        _assessment_for_student(
            db, tenant_id, student, external_id="UT-MATH-7B-T1", max_marks=100.0
        )

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-7B-T1",
            marks=60,
            external_id="score-api",
            source="erp",
        )
        apply_directory(db, tenant_id, directory, source="webhook")
        score = db.scalars(select(Score).where(Score.external_id == "score-api")).one()
        score.marks_obtained = 77
        score.source_system = ANALYTICS_SOURCE_SYSTEM
        db.commit()

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-7B-T1",
            marks=88,
            external_id="score-api",
            source="erp-2",
        )
        apply_directory(db, tenant_id, directory, source="webhook")
        conflict = db.scalars(
            select(ImportConflict).where(
                ImportConflict.tenant_id == tenant_id,
                ImportConflict.entity_id == score.id,
                ImportConflict.status == ImportConflictStatus.PENDING,
            )
        ).one()

        response = admin_client.post(
            f"/api/import/conflicts/{conflict.id}/resolve",
            json={"strategy": "accept_erp"},
        )
        assert response.status_code == 200
        db.refresh(score)
        assert score.marks_obtained == 88

        audit = db.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == SecurityAuditAction.IMPORT_CONFLICT_RESOLVE,
                SecurityAuditEvent.target_type == SecurityAuditTarget.IMPORT_CONFLICT,
            )
        ).one()
        assert audit.target_id == str(conflict.id)

    def test_per_field_resolution(self, db, tenant_bundle_dir, admin_client):
        tenant_id, directory = tenant_bundle_dir
        student = _student_in_section(db, grade=7, section_name="A")
        _enable_bulk_import(db)
        _assessment_for_student(
            db, tenant_id, student, external_id="UT-MATH-7A-T1", max_marks=100.0
        )

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-7A-T1",
            marks=50,
            external_id="score-pf",
            source="erp",
        )
        apply_directory(db, tenant_id, directory, source="webhook")
        score = db.scalars(select(Score).where(Score.external_id == "score-pf")).one()
        score.marks_obtained = 55
        score.source_system = ANALYTICS_SOURCE_SYSTEM
        db.commit()

        _apply_score_row(
            directory,
            student,
            assessment_code="UT-MATH-7A-T1",
            marks=65,
            external_id="score-pf",
            source="erp-2",
        )
        apply_directory(db, tenant_id, directory, source="webhook")
        conflict = db.scalars(
            select(ImportConflict).where(
                ImportConflict.tenant_id == tenant_id,
                ImportConflict.entity_id == score.id,
                ImportConflict.status == ImportConflictStatus.PENDING,
            )
        ).one()

        response = admin_client.post(
            f"/api/import/conflicts/{conflict.id}/resolve",
            json={
                "strategy": "per_field",
                "field_choices": {"marks_obtained": "local"},
            },
        )
        assert response.status_code == 200
        db.refresh(score)
        assert score.marks_obtained == 55

    def test_list_requires_bulk_import(self, admin_client, db):
        from tests.test_policy import _set_feature_flags

        _set_feature_flags(db, {"bulkImport": False})
        assert admin_client.get("/api/import/conflicts").status_code == 404
        _enable_bulk_import(db)
        assert admin_client.get("/api/import/conflicts").status_code == 200
