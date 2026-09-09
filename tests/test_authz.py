"""Authorization tests.

The point of these is that a plausible, well-formed request from a real signed-in user
must still be refused when it reaches outside their own scope. Guessing another
student's id is the obvious attack, so it is covered from every role.
"""

from __future__ import annotations

import pytest


class TestUnauthenticated:
    @pytest.mark.parametrize(
        "path", ["/parent", "/student", "/teacher", "/admin", "/admin/classes"]
    )
    def test_pages_redirect_to_login(self, anon_client, path):
        response = anon_client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/filters",
            "/api/charts/school.kpis",
            "/api/charts/student.kpis",
            "/export/school.kpis.csv",
        ],
    )
    def test_api_returns_401_rather_than_a_redirect(self, anon_client, path):
        assert anon_client.get(path).status_code == 401

    def test_root_sends_visitors_to_login(self, anon_client):
        response = anon_client.get("/")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    def test_login_page_is_reachable(self, anon_client):
        response = anon_client.get("/login")
        assert response.status_code == 200
        assert "Sign in" in response.text

    def test_wrong_password_is_rejected(self, anon_client, db):
        from sqlalchemy import select

        from app.models import Role, User

        email = db.scalars(
            select(User.email).where(User.role == Role.ADMIN).limit(1)
        ).one()
        response = anon_client.post(
            "/login", data={"email": email, "password": "not-the-password"}
        )
        assert response.status_code == 401
        assert "incorrect" in response.text.lower()

    def test_unknown_email_gives_the_same_message(self, anon_client):
        """An unknown account must be indistinguishable from a wrong password."""
        response = anon_client.post(
            "/login",
            data={"email": "no.such.person@sunrise.edu", "password": "whatever-value"},
        )
        assert response.status_code == 401
        assert "incorrect" in response.text.lower()

    def test_malformed_email_does_not_leak_a_different_message(self, anon_client):
        response = anon_client.post(
            "/login", data={"email": "not-an-email", "password": "whatever-value"}
        )
        assert response.status_code == 400
        assert "incorrect" in response.text.lower()

    def test_tampered_session_cookie_is_refused(self, anon_client):
        anon_client.cookies.set("sa_session", "forged.session.value")
        response = anon_client.get("/admin")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


class TestRoleSeparation:
    """Each portal belongs to exactly one role."""

    @pytest.mark.parametrize("path", ["/teacher", "/admin", "/student"])
    def test_parent_cannot_open_other_portals(self, parent_context, path):
        assert parent_context["client"].get(path).status_code == 403

    @pytest.mark.parametrize("path", ["/admin", "/parent", "/student"])
    def test_teacher_cannot_open_other_portals(self, teacher_context, path):
        assert teacher_context["client"].get(path).status_code == 403

    @pytest.mark.parametrize("path", ["/parent", "/teacher", "/admin"])
    def test_student_cannot_open_other_portals(self, student_context, path):
        assert student_context["client"].get(path).status_code == 403

    @pytest.mark.parametrize("path", ["/parent", "/teacher", "/student"])
    def test_admin_cannot_open_the_personal_portals(self, admin_client, path):
        assert admin_client.get(path).status_code == 403

    def test_parent_cannot_request_a_school_wide_chart(self, parent_context):
        response = parent_context["client"].get("/api/charts/school.kpis")
        assert response.status_code == 403

    def test_parent_cannot_request_a_classroom_chart(self, parent_context):
        response = parent_context["client"].get("/api/charts/section.students_table")
        assert response.status_code == 403

    def test_teacher_cannot_request_a_school_wide_chart(self, teacher_context):
        response = teacher_context["client"].get("/api/charts/school.at_risk")
        assert response.status_code == 403

    def test_teacher_cannot_export_a_school_wide_dataset(self, teacher_context):
        response = teacher_context["client"].get("/export/school.at_risk.csv")
        assert response.status_code == 403


class TestParentScope:
    def test_parent_sees_their_own_child(self, parent_context):
        response = parent_context["client"].get(
            "/api/charts/student.kpis",
            params={"student_id": parent_context["child_id"]},
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "kpi"

    def test_parent_cannot_read_another_child(self, parent_context):
        response = parent_context["client"].get(
            "/api/charts/student.kpis",
            params={"student_id": parent_context["other_student_id"]},
        )
        assert response.status_code == 404

    def test_parent_cannot_read_another_child_through_any_student_chart(
        self, parent_context
    ):
        for key in (
            "student.subject_table",
            "student.assessment_table",
            "student.remarks",
            "student.attendance",
            "student.vs_class",
            "student.term_progress",
        ):
            response = parent_context["client"].get(
                f"/api/charts/{key}",
                params={"student_id": parent_context["other_student_id"]},
            )
            assert response.status_code == 404, key

    def test_parent_cannot_export_another_child(self, parent_context):
        response = parent_context["client"].get(
            "/export/student.subject_table.csv",
            params={"student_id": parent_context["other_student_id"]},
        )
        assert response.status_code == 404

    def test_parent_page_with_a_foreign_student_id_is_refused(self, parent_context):
        response = parent_context["client"].get(
            "/parent", params={"student_id": parent_context["other_student_id"]}
        )
        assert response.status_code == 404

    def test_parent_filter_options_only_list_their_children(self, parent_context):
        options = parent_context["client"].get("/api/filters").json()
        listed = {entry["id"] for entry in options["students"]}
        assert parent_context["child_id"] in listed
        assert parent_context["other_student_id"] not in listed

    def test_parent_still_gets_a_real_class_benchmark(self, parent_context):
        """The class average must be computed from the whole class, not one child."""
        payload = parent_context["client"].get(
            "/api/charts/student.vs_class",
            params={"student_id": parent_context["child_id"]},
        ).json()
        labels = [series["label"] for series in payload["series"]]
        assert "Class average" in labels
        class_average = next(
            s for s in payload["series"] if s["label"] == "Class average"
        )
        assert any(value is not None for value in class_average["data"])

    def test_parent_cannot_see_named_classmates(self, parent_context):
        """The aggregate cohort view must never return other students by name."""
        from fastapi import HTTPException
        from sqlalchemy import select

        from app.analytics import queries
        from app.db import SessionLocal
        from app.deps import get_access_scope
        from app.models import Student, Tenant, User
        from app.schemas import FilterParams
        from app.tenant.context import TenantContext, set_tenant_context
        from app.tenant.settings import TenantSettings
        from seed.generate import SUNRISE_HOST

        class _Request:
            query_params: dict[str, str] = {}

        with SessionLocal() as session:
            guardian = session.scalars(
                select(User).where(User.email == parent_context["email"])
            ).one()
            tenant = session.scalars(
                select(Tenant).where(Tenant.key == "sunrise")
            ).one()
            set_tenant_context(
                TenantContext(
                    tenant_id=tenant.id,
                    tenant_key=tenant.key,
                    display_name=tenant.display_name,
                    hostname=SUNRISE_HOST,
                    settings=TenantSettings.from_json(tenant.settings_json),
                )
            )
            try:
                scope = get_access_scope(_Request(), session, guardian)
                cohort = scope.cohort_view()
                assert cohort.aggregate_only is True
                with pytest.raises(HTTPException) as refused:
                    queries.student_standings(session, FilterParams(), cohort)
                assert refused.value.status_code == 403
                child = session.get(Student, parent_context["child_id"])
                summaries = queries.student_subject_summaries(
                    session, child, FilterParams(), scope
                )
            finally:
                set_tenant_context(None)
            assert summaries
            assert any(s.cohort and s.cohort > 1 for s in summaries)


class TestStudentScope:
    def test_student_sees_themselves(self, student_context):
        response = student_context["client"].get("/api/charts/student.kpis")
        assert response.status_code == 200

    def test_student_cannot_read_a_classmate(self, student_context):
        response = student_context["client"].get(
            "/api/charts/student.kpis",
            params={"student_id": student_context["other_student_id"]},
        )
        assert response.status_code == 404

    def test_student_page_with_a_foreign_id_is_refused(self, student_context):
        response = student_context["client"].get(
            "/student/subjects",
            params={"student_id": student_context["other_student_id"]},
        )
        assert response.status_code == 404


class TestTeacherScope:
    def test_teacher_reads_their_own_class(self, teacher_context):
        response = teacher_context["client"].get(
            "/api/charts/section.kpis",
            params={"section_id": teacher_context["own_section_id"]},
        )
        assert response.status_code == 200

    def test_teacher_cannot_read_an_unassigned_class(self, teacher_context):
        if teacher_context["outside_section_id"] is None:
            pytest.skip("This teacher is assigned to every class.")
        response = teacher_context["client"].get(
            "/api/charts/section.kpis",
            params={"section_id": teacher_context["outside_section_id"]},
        )
        assert response.status_code == 404

    def test_teacher_cannot_read_a_student_they_do_not_teach(self, teacher_context):
        if teacher_context["outside_student_id"] is None:
            pytest.skip("This teacher teaches every student.")
        response = teacher_context["client"].get(
            "/api/charts/student.kpis",
            params={"student_id": teacher_context["outside_student_id"]},
        )
        assert response.status_code == 404

    def test_teacher_student_detail_page_is_scoped(self, teacher_context):
        if teacher_context["outside_student_id"] is None:
            pytest.skip("This teacher teaches every student.")
        allowed = teacher_context["client"].get(
            f"/teacher/student/{teacher_context['own_student_id']}"
        )
        assert allowed.status_code == 200
        refused = teacher_context["client"].get(
            f"/teacher/student/{teacher_context['outside_student_id']}"
        )
        assert refused.status_code == 404

    def test_teacher_filter_options_only_list_their_classes(self, teacher_context):
        options = teacher_context["client"].get("/api/filters").json()
        listed = {entry["id"] for entry in options["sections"]}
        assert teacher_context["own_section_id"] in listed
        if teacher_context["outside_section_id"] is not None:
            assert teacher_context["outside_section_id"] not in listed

    def test_teacher_classroom_data_excludes_outside_students(self, teacher_context):
        payload = teacher_context["client"].get(
            "/api/charts/section.students_table",
            params={"section_id": teacher_context["own_section_id"]},
        ).json()
        ids = {row["_student_id"] for row in payload["rows"]}
        if teacher_context["outside_student_id"] is not None:
            assert teacher_context["outside_student_id"] not in ids


class TestAdminScope:
    def test_admin_reads_the_whole_school(self, admin_client):
        response = admin_client.get("/api/charts/school.kpis")
        assert response.status_code == 200
        assert response.json()["cards"]

    def test_admin_can_open_any_student(self, admin_client, parent_context):
        response = admin_client.get(
            f"/admin/student/{parent_context['other_student_id']}"
        )
        assert response.status_code == 200

    def test_admin_sees_every_class_in_the_filters(self, admin_client, db):
        from sqlalchemy import func, select

        from app.models import Section

        total = db.scalar(select(func.count(Section.id)))
        options = admin_client.get("/api/filters").json()
        assert len(options["sections"]) == total


class TestInputValidation:
    def test_unknown_chart_key_is_a_404(self, admin_client):
        assert admin_client.get("/api/charts/school.not_a_chart").status_code == 404

    @pytest.mark.parametrize(
        "key", ["../secrets", "school/kpis", "SCHOOL.KPIS", "school.kpis;drop"]
    )
    def test_malformed_chart_keys_are_rejected(self, admin_client, key):
        response = admin_client.get(f"/api/charts/{key}")
        assert response.status_code in (404, 422)

    def test_non_numeric_filter_is_rejected(self, admin_client):
        response = admin_client.get(
            "/api/charts/school.kpis", params={"section_id": "not-a-number"}
        )
        assert response.status_code == 422

    def test_zero_and_negative_ids_are_rejected(self, admin_client):
        for value in (0, -5):
            response = admin_client.get(
                "/api/charts/school.kpis", params={"grade_id": value}
            )
            assert response.status_code == 422

    def test_inverted_date_window_is_rejected(self, admin_client):
        response = admin_client.get(
            "/api/charts/school.kpis",
            params={"date_from": "2026-01-01", "date_to": "2025-01-01"},
        )
        assert response.status_code == 422

    def test_unknown_assessment_type_is_rejected(self, admin_client):
        response = admin_client.get(
            "/api/charts/school.kpis", params={"assessment_type": "pop_quiz"}
        )
        assert response.status_code == 422

    def test_unknown_portal_page_is_a_404(self, admin_client):
        assert admin_client.get("/admin/nonsense").status_code == 404


class TestSessionLifecycle:
    def test_logout_clears_the_session(self, admin_client):
        assert admin_client.get("/api/charts/school.kpis").status_code == 200
        admin_client.get("/logout")
        assert admin_client.get("/api/charts/school.kpis").status_code == 401

    def test_session_cookie_is_http_only_and_same_site(self, anon_client, db):
        from sqlalchemy import select

        from app.models import Role, User
        from seed.generate import DEMO_PASSWORD

        email = db.scalars(
            select(User.email).where(User.role == Role.ADMIN).limit(1)
        ).one()
        response = anon_client.post(
            "/login", data={"email": email, "password": DEMO_PASSWORD}
        )
        cookie_header = response.headers["set-cookie"].lower()
        assert "httponly" in cookie_header
        assert "samesite=strict" in cookie_header

    def test_logout_deletion_mirrors_the_cookie_attributes(self, admin_client):
        """A deletion that drops Secure is ignored for a Secure cookie.

        The token is signed and self-contained with no server-side revocation, so
        a cookie that survives logout stays usable until it expires.
        """
        from app.config import settings

        original = settings.session_cookie_secure
        settings.session_cookie_secure = True
        try:
            header = admin_client.get("/logout").headers["set-cookie"].lower()
        finally:
            settings.session_cookie_secure = original

        assert "secure" in header, header
        assert "samesite=strict" in header, header
        assert "httponly" in header, header
        # An immediate expiry is what actually removes it.
        assert "max-age=0" in header or "expires=thu, 01 jan 1970" in header, header

    def test_expired_session_redirect_also_clears_the_cookie(self, anon_client):
        """The same attribute rule applies where a stale cookie is thrown away."""
        from app.config import settings

        anon_client.cookies.set(settings.session_cookie_name, "not-a-valid-token")
        original = settings.session_cookie_secure
        settings.session_cookie_secure = True
        try:
            response = anon_client.get("/admin", follow_redirects=False)
        finally:
            settings.session_cookie_secure = original

        assert response.status_code == 303
        header = response.headers["set-cookie"].lower()
        assert "secure" in header and "samesite=strict" in header, header


class TestSecurityHeaders:
    def test_headers_are_present(self, admin_client):
        response = admin_client.get("/admin")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "content-security-policy" in response.headers
