"""Regression: termless published remarks on full-year parent timeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import AcademicYear, Remark, RemarkCategory, RemarkVisibility, Student
from tests.test_f07_parent_remark_notices import _shared_student
from tests.test_policy import _set_feature_flags


@pytest.fixture(autouse=True)
def _remarks_enabled_for_suite(db):
    """``test_policy`` may leave remarks disabled on the shared tenant."""
    _set_feature_flags(db, {"remarks": True, "parentPortal": True})


class TestTermlessPublishedFullYear:
    def test_published_without_term_visible_on_full_year_timeline(
        self, teacher_context, parent_context, db
    ):
        """Teachers may leave term unset on Full year; guardians must still see it."""
        student_id = _shared_student(teacher_context, parent_context, db)
        marker = "She has won the Maths Olympiad regression."
        created = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "body": marker,
                "category": "achievement",
                "publish": True,
            },
        )
        assert created.status_code == 200

        items = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert any(item["body"] == marker for item in items), (
            "termless published remark dropped by academic-year filter"
        )

    def test_legacy_termless_after_year_end_visible_without_republishing(
        self, parent_context, db
    ):
        """Existing rows with NULL term_id and a late publish date still match the year."""
        year = db.scalars(
            select(AcademicYear).order_by(AcademicYear.is_current.desc()).limit(1)
        ).one()
        child_id = parent_context["child_id"]
        child = db.get(Student, child_id)
        assert child is not None
        # Demo data ends the academic year in March; achievements may be published later.
        after_year_end = datetime(
            year.end_date.year,
            year.end_date.month,
            year.end_date.day,
            tzinfo=UTC,
        ) + timedelta(days=220)
        marker = "Olympiad medal after academic year ended (legacy term_id NULL)."

        remark = Remark(
            tenant_id=child.tenant_id,
            student_id=child_id,
            teacher_id=None,
            term_id=None,
            category=RemarkCategory.ACHIEVEMENT,
            visibility=RemarkVisibility.PUBLISHED,
            body=marker,
            created_at=after_year_end,
            published_at=after_year_end,
        )
        db.add(remark)
        db.commit()

        response = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": child_id},
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert any(item["body"] == marker for item in items), (
            "legacy termless remark after year end dropped instead of inferred to year"
        )
