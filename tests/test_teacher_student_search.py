"""Teacher-scoped student search (F05-01)."""

from __future__ import annotations


class TestTeacherStudentSearchApi:
    def test_teacher_search_lists_only_assigned_students(self, teacher_context, db):
        from app.models import Student

        own_id = teacher_context["own_student_id"]
        own = db.get(Student, own_id)
        prefix = own.admission_no[:4].lower()

        payload = teacher_context["client"].get(
            "/api/teacher/students/search", params={"q": prefix}
        ).json()
        assert payload["results"]
        ids = {row["id"] for row in payload["results"]}
        assert own_id in ids
        if teacher_context["outside_student_id"] is not None:
            assert teacher_context["outside_student_id"] not in ids

    def test_teacher_search_omits_contact_email(self, teacher_context):
        payload = teacher_context["client"].get(
            "/api/teacher/students/search", params={"q": ""}
        ).json()
        for row in payload["results"]:
            assert "email" not in row

    def test_teacher_search_browse_without_query(self, teacher_context):
        response = teacher_context["client"].get(
            "/api/teacher/students/search", params={"q": ""}
        )
        assert response.status_code == 200
        assert isinstance(response.json()["results"], list)

    def test_admin_endpoint_still_denies_teacher(self, teacher_context):
        response = teacher_context["client"].get(
            "/api/students/search", params={"q": "aa"}
        )
        assert response.status_code == 403

    def test_out_of_scope_student_id_still_404(self, teacher_context):
        outside = teacher_context.get("outside_student_id")
        if outside is None:
            return
        response = teacher_context["client"].get(f"/teacher/student/{outside}")
        assert response.status_code == 404

    def test_parent_cannot_use_teacher_search(self, parent_context):
        response = parent_context["client"].get(
            "/api/teacher/students/search", params={"q": "aa"}
        )
        assert response.status_code == 403

    def test_teacher_lookup_page_renders_combobox(self, teacher_context):
        response = teacher_context["client"].get("/teacher/lookup")
        assert response.status_code == 200
        assert "data-student-combobox" in response.text
        assert "/api/teacher/students/search" in response.text
