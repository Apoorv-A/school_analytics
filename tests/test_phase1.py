"""Phase 1 thin-slice acceptance tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.analytics import metrics
from app.models import Student


class TestStudentSearch:
    def test_admin_search_by_admission_prefix(self, admin_client, db):
        student = db.scalars(select(Student).limit(1)).one()
        prefix = student.admission_no[:4]
        response = admin_client.get(
            "/api/students/search", params={"q": prefix}
        )
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["results"]}
        assert student.id in ids

    def test_short_query_returns_empty(self, admin_client):
        response = admin_client.get("/api/students/search", params={"q": "zzzznotfound"})
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_teacher_cannot_search(self, teacher_context):
        response = teacher_context["client"].get(
            "/api/students/search", params={"q": "STU"}
        )
        assert response.status_code == 403


class TestMultiFilters:
    def test_subject_ids_in_chart_query(self, admin_client, db):
        from app.models import Subject

        subjects = db.scalars(select(Subject.id).limit(2)).all()
        if len(subjects) < 2:
            pytest.skip("need two subjects")
        response = admin_client.get(
            "/api/charts/school.subject_averages",
            params=[("subject_ids", subjects[0]), ("subject_ids", subjects[1])],
        )
        assert response.status_code == 200

    def test_zero_id_in_subject_ids_rejected(self, admin_client):
        response = admin_client.get(
            "/api/charts/school.subject_averages",
            params=[("subject_ids", 0)],
        )
        assert response.status_code == 422

    def test_zero_id_in_section_ids_rejected(self, admin_client):
        response = admin_client.get(
            "/api/charts/school.section_compare",
            params=[("section_ids", 0)],
        )
        assert response.status_code == 422

    def test_cross_grade_sections_rejected(self, admin_client, db):
        from app.models import Grade, Section

        grades = db.scalars(select(Grade.id).limit(2)).all()
        if len(grades) < 2:
            pytest.skip("need two grades")
        sections = []
        for grade_id in grades:
            sid = db.scalars(
                select(Section.id).where(Section.grade_id == grade_id).limit(1)
            ).first()
            if sid:
                sections.append(sid)
        if len(sections) < 2:
            pytest.skip("need sections in two grades")
        response = admin_client.get(
            "/api/charts/school.section_compare",
            params=[("section_ids", sections[0]), ("section_ids", sections[1])],
        )
        assert response.status_code == 422


class TestStudentInsights:
    def test_insights_chart_payload(self, admin_client, db):
        student = db.scalars(select(Student).limit(1)).one()
        response = admin_client.get(
            "/api/charts/student.insights",
            params={"student_id": student.id},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "insights"
        assert "actions" in body
        assert len(body["actions"]) >= 1

    def test_assess_risk_concern_signal(self):
        assessment = metrics.assess_risk(
            55.0, None, 90.0, 0, concern_remark_count=1
        )
        assert any("concern remark" in r for r in assessment.reasons)


class TestRemarksApi:
    def test_teacher_can_post_remark(self, teacher_context, db):
        client = teacher_context["client"]
        student_id = teacher_context["own_student_id"]
        response = client.post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "body": "Good progress this week.",
                "category": "academic",
            },
        )
        assert response.status_code == 200
        assert response.json()["student_id"] == student_id

    def test_teacher_cannot_remark_outside_section(self, teacher_context):
        outside = teacher_context.get("outside_student_id")
        if outside is None:
            pytest.skip("no outside student")
        response = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": outside,
                "body": "Should not stick.",
                "category": "concern",
            },
        )
        assert response.status_code == 404

    def test_teacher_cannot_attach_unknown_term(self, teacher_context):
        response = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": teacher_context["own_student_id"],
                "body": "Term should not stick.",
                "category": "academic",
                "term_id": 9_999_999,
            },
        )
        assert response.status_code == 404

    def test_teacher_cannot_attach_another_tenants_term(self, teacher_context, db):
        from sqlalchemy import select

        from app.models import Tenant, Term

        horizon = db.scalars(select(Tenant).where(Tenant.key == "horizon")).one()
        foreign_term_id = db.scalars(
            select(Term.id).where(Term.tenant_id == horizon.id).limit(1)
        ).one()
        response = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": teacher_context["own_student_id"],
                "body": "Cross-tenant term should not stick.",
                "category": "academic",
                "term_id": foreign_term_id,
            },
        )
        assert response.status_code == 404


class TestImportValidate:
    def test_validate_missing_dir(self, tmp_path):
        from app.import_.validate import validate_directory

        report = validate_directory(tmp_path / "missing")
        assert report["valid"] is False
        assert any("No recognized CSV files" in err["error"] for err in report["errors"])
