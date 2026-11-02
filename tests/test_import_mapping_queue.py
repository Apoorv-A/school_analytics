"""Assessment import mapping queue and admin resolution API."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.audit import SecurityAuditAction, SecurityAuditTarget
from app.import_.apply import apply_directory
from app.models import (
    AcademicYear,
    Assessment,
    AssessmentType,
    ImportMappingQueue,
    ImportMappingQueueStatus,
    Score,
    Section,
    SecurityAuditEvent,
    Student,
    Subject,
    Tenant,
    Term,
)
from tests.test_import_apply import _enable_bulk_import, _write_csv


@pytest.fixture
def tenant_bundle_dir(tmp_path, db):
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    return tenant.id, tmp_path


def _student_grade_6a(db) -> Student:
    from app.models import Grade

    return db.scalars(
        select(Student)
        .join(Section, Student.section_id == Section.id)
        .join(Grade, Section.grade_id == Grade.id)
        .where(Grade.level == 6, Section.name == "A")
        .limit(1)
    ).one()


def _create_assessment(db, tenant_id, student: Student) -> Assessment:
    section = db.get(Section, student.section_id)
    subject = db.scalars(select(Subject).limit(1)).one()
    year = db.get(AcademicYear, section.academic_year_id)
    term = db.scalars(
        select(Term).where(Term.academic_year_id == year.id).limit(1)
    ).one()
    assessment = Assessment(
        tenant_id=tenant_id,
        name="Mapped assessment",
        assessment_type=AssessmentType.UNIT_TEST,
        subject_id=subject.id,
        section_id=section.id,
        term_id=term.id,
        max_marks=100.0,
        conducted_on=date(2025, 8, 1),
    )
    db.add(assessment)
    db.commit()
    return assessment


class TestImportMappingQueue:
    def test_unknown_assessment_queued_not_created(self, db, tenant_bundle_dir):
        tenant_id, directory = tenant_bundle_dir
        student = _student_grade_6a(db)

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},ERP-UT-MATH-6A-T1,38,false,score-new,erp",
        )
        report = apply_directory(db, tenant_id, directory, source="webhook")
        assert report["rows"]["scores.csv"] == 0

        assert (
            db.scalars(
                select(Assessment).where(
                    Assessment.tenant_id == tenant_id,
                    Assessment.external_id == "ERP-UT-MATH-6A-T1",
                )
            ).first()
            is None
        )
        queued = db.scalars(
            select(ImportMappingQueue).where(
                ImportMappingQueue.tenant_id == tenant_id,
                ImportMappingQueue.external_key == "ERP-UT-MATH-6A-T1",
            )
        ).one()
        assert queued.status == ImportMappingQueueStatus.PENDING
        assert queued.source == "webhook"

    def test_mapped_import_succeeds(self, db, tenant_bundle_dir, admin_client):
        tenant_id, directory = tenant_bundle_dir
        student = _student_grade_6a(db)
        assessment = _create_assessment(db, tenant_id, student)
        _enable_bulk_import(db)

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},ERP-UT-MATH-6A-T1,38,false,score-new,erp",
        )
        first_report = apply_directory(db, tenant_id, directory)
        assert first_report["rows"]["scores.csv"] == 0

        queued = db.scalars(
            select(ImportMappingQueue).where(
                ImportMappingQueue.tenant_id == tenant_id,
                ImportMappingQueue.external_key == "ERP-UT-MATH-6A-T1",
            )
        ).one()

        response = admin_client.post(
            f"/api/import/mapping-queue/{queued.id}/map",
            json={"assessment_id": assessment.id},
            headers={"X-Correlation-ID": "test-map-1"},
        )
        assert response.status_code == 200
        assert response.json()["external_id"] == "ERP-UT-MATH-6A-T1"

        event = db.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.tenant_id == tenant_id,
                SecurityAuditEvent.action == SecurityAuditAction.IMPORT_MAPPING_MAP,
                SecurityAuditEvent.correlation_id == "test-map-1",
            )
        ).one()
        assert event.target_type == SecurityAuditTarget.IMPORT_MAPPING
        assert event.target_id == str(queued.id)

        second_report = apply_directory(db, tenant_id, directory)
        assert second_report["rows"]["scores.csv"] == 1
        score = db.scalars(
            select(Score).where(
                Score.student_id == student.id,
                Score.assessment_id == assessment.id,
            )
        ).one()
        assert score.marks_obtained == 38.0

    def test_reject_mapping_records_audit(self, db, tenant_bundle_dir, admin_client):
        tenant_id, directory = tenant_bundle_dir
        student = _student_grade_6a(db)
        _enable_bulk_import(db)

        _write_csv(
            directory / "scores.csv",
            "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
            f"{student.admission_no},REJECT-KEY-1,10,false,score-reject,erp",
        )
        apply_directory(db, tenant_id, directory)
        queued = db.scalars(
            select(ImportMappingQueue).where(
                ImportMappingQueue.external_key == "REJECT-KEY-1",
            )
        ).one()

        response = admin_client.post(
            f"/api/import/mapping-queue/{queued.id}/reject",
            headers={"X-Correlation-ID": "test-reject-1"},
        )
        assert response.status_code == 200
        assert response.json()["item"]["status"] == "rejected"

        event = db.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == SecurityAuditAction.IMPORT_MAPPING_REJECT,
                SecurityAuditEvent.correlation_id == "test-reject-1",
            )
        ).one()
        assert event.target_id == str(queued.id)

        db.expire_all()
        report = apply_directory(db, tenant_id, directory)
        warnings = report.get("warnings", [])
        assert any("rejected by an administrator" in item["error"] for item in warnings)

    def test_list_queue_requires_bulk_import(self, admin_client, db):
        from tests.test_policy import _set_feature_flags

        _set_feature_flags(db, {"bulkImport": False})
        response = admin_client.get("/api/import/mapping-queue")
        assert response.status_code == 404
