"""Regression: termless published remarks on full-year parent timeline."""

from __future__ import annotations

from tests.test_f07_parent_remark_notices import _shared_student


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
