"""F06-03: teacher remarks composer page and API wiring."""

from __future__ import annotations

from tests.test_f06_remarks_publication import (
    _remark_student_id,
    _remark_term_id,
)


class TestTeacherRemarksComposerPage:
    def test_teacher_composer_page_smoke(self, teacher_context):
        response = teacher_context["client"].get("/teacher/remarks")
        assert response.status_code == 200
        text = response.text
        assert "remark-composer" in text
        assert "Family view preview" in text
        assert "remark-family-preview" in text
        assert "/static/js/remarks_composer.js" in text
        assert "Save draft" in text
        assert "Save &amp; publish" in text or "Save & publish" in text

    def test_parent_cannot_open_teacher_composer(self, parent_context):
        response = parent_context["client"].get(
            "/teacher/remarks",
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 403

    def test_teacher_nav_lists_write_remark(self, teacher_context):
        response = teacher_context["client"].get("/teacher")
        assert response.status_code == 200
        assert 'href="/teacher/remarks"' in response.text
        assert "Write remark" in response.text


class TestTeacherRemarksComposerIntegration:
    def test_composer_flow_draft_then_publish_visible_to_parent(
        self, teacher_context, parent_context, db
    ):
        student_id = _remark_student_id(teacher_context, parent_context, db)
        term_id = _remark_term_id(db, student_id)
        marker = "F06-03 composer published remark."

        draft = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "term_id": term_id,
                "body": marker,
                "category": "academic",
            },
        )
        assert draft.status_code == 200
        remark_id = draft.json()["id"]

        parent_before = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert not any(item["body"] == marker for item in parent_before)

        published = teacher_context["client"].post(f"/api/remarks/{remark_id}/publish")
        assert published.status_code == 200

        parent_after = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert any(item["body"] == marker for item in parent_after)

    def test_composer_publish_on_create_skips_second_step(
        self, teacher_context, parent_context, db
    ):
        student_id = _remark_student_id(teacher_context, parent_context, db)
        term_id = _remark_term_id(db, student_id)
        marker = "F06-03 direct publish."
        created = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "term_id": term_id,
                "body": marker,
                "category": "achievement",
                "publish": True,
            },
        )
        assert created.status_code == 200
        parent_items = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert any(item["body"] == marker for item in parent_items)
