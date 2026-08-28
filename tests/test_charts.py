"""End-to-end coverage of every registered chart, page, and export.

The registry is walked directly, so adding a chart without a working handler, or
without deciding which roles may see it, fails the suite rather than the dashboard.
"""

from __future__ import annotations

import pytest

from app.analytics.charts import CHART_REGISTRY
from app.models import Role

STUDENT_KEYS = sorted(k for k in CHART_REGISTRY if k.startswith("student."))
SECTION_KEYS = sorted(k for k in CHART_REGISTRY if k.startswith("section."))
SCHOOL_KEYS = sorted(k for k in CHART_REGISTRY if k.startswith("school."))

VALID_KINDS = {
    "kpi", "line", "bar", "stacked-bar", "doughnut", "gauge",
    "radar", "scatter", "heatmap", "table", "timeline",
}


def assert_valid_payload(payload: dict, key: str) -> None:
    """Every payload must be something the front end knows how to draw."""
    assert payload["kind"] in VALID_KINDS, f"{key}: unknown kind {payload['kind']}"
    assert payload["key"] == key
    assert payload["title"]

    kind = payload["kind"]
    if kind == "kpi":
        assert payload["cards"], f"{key}: no KPI cards"
        for card in payload["cards"]:
            assert card["label"] and card["value"] is not None
    elif kind == "table":
        assert payload["columns"], f"{key}: table has no columns"
        keys = {column["key"] for column in payload["columns"]}
        for row in payload["rows"]:
            missing = keys - set(row)
            assert not missing, f"{key}: row missing {missing}"
    elif kind == "heatmap":
        assert len(payload["values"]) == len(payload["rows"])
        for row in payload["values"]:
            assert len(row) == len(payload["columns"])
    elif kind == "timeline":
        assert isinstance(payload["items"], list)
    elif kind == "scatter":
        assert payload["series"]
        for point in payload["series"][0]["data"]:
            assert "x" in point and "y" in point
    else:
        assert payload["series"], f"{key}: no series"
        for series in payload["series"]:
            assert series["label"]
            assert len(series["data"]) == len(payload["labels"]), (
                f"{key}: series '{series['label']}' length does not match labels"
            )


class TestRegistry:
    def test_every_chart_declares_roles_and_a_handler(self):
        for key, definition in CHART_REGISTRY.items():
            assert definition.roles, f"{key} has no roles"
            assert callable(definition.handler), f"{key} has no handler"
            assert definition.title, f"{key} has no title"

    def test_school_charts_are_admin_only(self):
        for key in SCHOOL_KEYS:
            assert CHART_REGISTRY[key].roles == frozenset({Role.ADMIN}), key

    def test_classroom_charts_exclude_families(self):
        for key in SECTION_KEYS:
            roles = CHART_REGISTRY[key].roles
            assert Role.PARENT not in roles and Role.STUDENT not in roles, key

    def test_listing_is_filtered_by_role(self, admin_client, parent_context):
        admin_keys = {c["key"] for c in admin_client.get("/api/charts").json()["charts"]}
        parent_keys = {
            c["key"]
            for c in parent_context["client"].get("/api/charts").json()["charts"]
        }
        assert SCHOOL_KEYS[0] in admin_keys
        assert not (parent_keys & set(SCHOOL_KEYS))
        assert parent_keys == set(STUDENT_KEYS)


class TestStudentCharts:
    @pytest.mark.parametrize("key", STUDENT_KEYS)
    def test_parent_can_render_every_student_chart(self, parent_context, key):
        response = parent_context["client"].get(
            f"/api/charts/{key}", params={"student_id": parent_context["child_id"]}
        )
        assert response.status_code == 200, f"{key}: {response.text[:200]}"
        assert_valid_payload(response.json(), key)

    @pytest.mark.parametrize("key", STUDENT_KEYS)
    def test_student_can_render_every_student_chart(self, student_context, key):
        response = student_context["client"].get(f"/api/charts/{key}")
        assert response.status_code == 200, f"{key}: {response.text[:200]}"
        assert_valid_payload(response.json(), key)

    def test_staff_open_on_a_default_student(self, admin_client):
        """The lookup page must show a record straight away, not an error."""
        response = admin_client.get("/api/charts/student.kpis")
        assert response.status_code == 200, response.text[:200]
        assert response.json()["meta"]["student"]

    def test_default_student_follows_the_grade_filter(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Grade, Section, Student

        grade_id, grade_name = db.execute(
            select(Grade.id, Grade.name).order_by(Grade.level.desc()).limit(1)
        ).one()
        allowed = set(
            db.scalars(
                select(Student.full_name)
                .join(Section, Student.section_id == Section.id)
                .where(Section.grade_id == grade_id)
            )
        )
        meta = admin_client.get(
            "/api/charts/student.kpis", params={"grade_id": grade_id}
        ).json()["meta"]
        assert meta["student"] in allowed
        assert grade_name in meta["section"]

    def test_teacher_default_student_stays_inside_their_sections(
        self, teacher_context, db
    ):
        from sqlalchemy import select

        from app.models import Student

        meta = teacher_context["client"].get("/api/charts/student.kpis").json()["meta"]
        own = set(
            db.scalars(
                select(Student.full_name).where(
                    Student.section_id.in_(teacher_context["section_ids"])
                )
            )
        )
        assert meta["student"] in own

    def test_subject_filter_switches_the_trend_axis(self, parent_context, db):
        from sqlalchemy import select

        from app.models import Subject

        client = parent_context["client"]
        subject_id = db.scalars(select(Subject.id).order_by(Subject.id).limit(1)).one()

        by_term = client.get(
            "/api/charts/student.subject_trend",
            params={"student_id": parent_context["child_id"]},
        ).json()
        assert by_term["meta"]["axis"] == "Term"

        by_assessment = client.get(
            "/api/charts/student.subject_trend",
            params={
                "student_id": parent_context["child_id"],
                "subject_id": subject_id,
            },
        ).json()
        assert by_assessment["meta"]["axis"] == "Assessment"
        assert len(by_assessment["labels"]) > len(by_term["labels"])


class TestSectionCharts:
    @pytest.mark.parametrize("key", SECTION_KEYS)
    def test_teacher_can_render_every_classroom_chart(self, teacher_context, key):
        response = teacher_context["client"].get(
            f"/api/charts/{key}",
            params={"section_id": teacher_context["own_section_id"]},
        )
        assert response.status_code == 200, f"{key}: {response.text[:200]}"
        assert_valid_payload(response.json(), key)

    @pytest.mark.parametrize("key", SECTION_KEYS)
    def test_classroom_charts_work_without_an_explicit_section(
        self, teacher_context, key
    ):
        response = teacher_context["client"].get(f"/api/charts/{key}")
        assert response.status_code == 200, f"{key}: {response.text[:200]}"

    @pytest.mark.parametrize("key", SECTION_KEYS)
    def test_principal_can_render_classroom_charts_without_a_section(
        self, admin_client, key
    ):
        """A principal has no home classroom, so one is chosen for them."""
        response = admin_client.get(f"/api/charts/{key}")
        assert response.status_code == 200, f"{key}: {response.text[:200]}"
        payload = response.json()
        assert_valid_payload(payload, key)
        # The payload must say which classroom it settled on.
        assert payload["meta"]["hint"].startswith("Grade "), payload["meta"]

    def test_grade_filter_picks_a_classroom_in_that_grade(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Grade, Section

        grade_id, grade_name = db.execute(
            select(Grade.id, Grade.name).order_by(Grade.level.desc()).limit(1)
        ).one()
        section_ids = set(
            db.scalars(select(Section.id).where(Section.grade_id == grade_id))
        )
        payload = admin_client.get(
            "/api/charts/section.students_table", params={"grade_id": grade_id}
        ).json()
        assert payload["rows"]
        assert grade_name in payload["meta"]["hint"], payload["meta"]

        # Every listed student must belong to a classroom inside the filtered grade.
        from app.models import Student

        listed = {row["name"] for row in payload["rows"]}
        allowed = set(
            db.scalars(
                select(Student.full_name).where(Student.section_id.in_(section_ids))
            )
        )
        assert listed <= allowed


class TestSchoolCharts:
    @pytest.mark.parametrize("key", SCHOOL_KEYS)
    def test_admin_can_render_every_school_chart(self, admin_client, key):
        response = admin_client.get(f"/api/charts/{key}")
        assert response.status_code == 200, f"{key}: {response.text[:200]}"
        assert_valid_payload(response.json(), key)

    def test_school_average_is_plausible(self, admin_client):
        cards = admin_client.get("/api/charts/school.kpis").json()["cards"]
        average = next(c for c in cards if c["label"] == "School average")
        value = float(average["value"].rstrip("%"))
        assert 0 < value < 100

    def test_term_trend_ignores_a_single_term_filter(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Term

        term_id = db.scalars(select(Term.id).order_by(Term.sequence).limit(1)).one()
        payload = admin_client.get(
            "/api/charts/school.term_trend", params={"term_id": term_id}
        ).json()
        assert len(payload["labels"]) > 1

    def test_at_risk_rows_carry_reasons(self, admin_client):
        payload = admin_client.get("/api/charts/school.at_risk").json()
        assert payload["rows"], "expected the seeded data to contain at-risk students"
        for row in payload["rows"]:
            assert row["reasons"] and row["reasons"] != "--"
            assert row["status"] in ("Needs watching", "At risk")


class TestNaturalOrdering:
    """Grades and terms must read in their own order, not alphabetically.

    Sorting these by label puts "Grade 10" before "Grade 6", which makes a
    grade-progression chart misleading.
    """

    def _grade_levels(self, labels: list[str]) -> list[int]:
        return [int(label.rsplit(" ", 1)[-1]) for label in labels]

    def test_grade_averages_follow_grade_level(self, admin_client):
        labels = admin_client.get("/api/charts/school.grade_averages").json()["labels"]
        levels = self._grade_levels(labels)
        assert levels == sorted(levels), labels

    def test_term_trend_follows_term_sequence(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Term

        expected = list(db.scalars(select(Term.name).order_by(Term.sequence)))
        labels = admin_client.get("/api/charts/school.term_trend").json()["labels"]
        assert labels == expected

    def test_section_matrix_rows_follow_grade_level(self, admin_client):
        payload = admin_client.get("/api/charts/school.section_matrix").json()
        labels = [row["label"] for row in payload["rows"]]
        keys = [
            (int(label.split(" - ")[0].rsplit(" ", 1)[-1]), label.split(" - ")[1])
            for label in labels
        ]
        assert keys == sorted(keys), labels

    def test_matrix_values_align_with_rows_and_columns(self, admin_client):
        payload = admin_client.get("/api/charts/school.section_matrix").json()
        assert len(payload["values"]) == len(payload["rows"])
        for row_values in payload["values"]:
            assert len(row_values) == len(payload["columns"])


class TestFiltersApplyEverywhere:
    """A filter must actually narrow the result, not be silently ignored."""

    def test_term_filter_reduces_the_result_count(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Term

        term_id = db.scalars(select(Term.id).order_by(Term.sequence).limit(1)).one()
        full_year = admin_client.get("/api/charts/school.kpis").json()
        one_term = admin_client.get(
            "/api/charts/school.kpis", params={"term_id": term_id}
        ).json()

        def results(payload):
            card = next(c for c in payload["cards"] if c["label"] == "Pass rate")
            return int(card["hint"].split()[0])

        assert results(one_term) < results(full_year)

    def test_grade_filter_narrows_the_class_list(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Grade

        grade_id = db.scalars(select(Grade.id).order_by(Grade.level).limit(1)).one()
        all_classes = admin_client.get(
            "/api/charts/school.section_leaderboard"
        ).json()
        one_grade = admin_client.get(
            "/api/charts/school.section_leaderboard", params={"grade_id": grade_id}
        ).json()
        assert len(one_grade["rows"]) < len(all_classes["rows"])

    def test_subject_filter_narrows_the_subject_chart(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Subject

        subject_id = db.scalars(select(Subject.id).order_by(Subject.id).limit(1)).one()
        payload = admin_client.get(
            "/api/charts/school.subject_averages", params={"subject_id": subject_id}
        ).json()
        assert len(payload["labels"]) == 1

    def test_assessment_type_filter_is_applied(self, admin_client):
        payload = admin_client.get(
            "/api/charts/school.assessment_type_averages",
            params={"assessment_type": "final"},
        ).json()
        assert payload["labels"] == ["Final Exam"]

    def test_date_window_narrows_the_assessment_list(self, teacher_context):
        params = {"section_id": teacher_context["own_section_id"]}
        everything = teacher_context["client"].get(
            "/api/charts/section.assessment_averages", params=params
        ).json()
        windowed = teacher_context["client"].get(
            "/api/charts/section.assessment_averages",
            params={**params, "date_from": "2025-06-01", "date_to": "2025-09-30"},
        ).json()
        assert 0 < len(windowed["labels"]) < len(everything["labels"])

    def test_impossible_filter_combination_returns_an_empty_payload(
        self, admin_client
    ):
        payload = admin_client.get(
            "/api/charts/school.subject_averages",
            params={"date_from": "2030-01-01", "date_to": "2030-12-31"},
        ).json()
        assert payload["labels"] == []


class TestPortalPages:
    PARENT_PAGES = [
        "/parent", "/parent/subjects", "/parent/assessments",
        "/parent/progress", "/parent/attendance", "/parent/remarks",
    ]
    STUDENT_PAGES = [
        "/student", "/student/subjects", "/student/assessments",
        "/student/progress", "/student/attendance", "/student/remarks",
    ]
    TEACHER_PAGES = [
        "/teacher", "/teacher/assessments", "/teacher/subjects",
        "/teacher/students", "/teacher/attention", "/teacher/attendance",
    ]
    ADMIN_PAGES = [
        "/admin", "/admin/grades", "/admin/subjects", "/admin/classes",
        "/admin/attention", "/admin/teachers", "/admin/student",
    ]

    @pytest.mark.parametrize("path", PARENT_PAGES)
    def test_parent_pages_render(self, parent_context, path):
        response = parent_context["client"].get(path)
        assert response.status_code == 200
        assert "data-chart" in response.text

    @pytest.mark.parametrize("path", STUDENT_PAGES)
    def test_student_pages_render(self, student_context, path):
        response = student_context["client"].get(path)
        assert response.status_code == 200
        assert "data-chart" in response.text

    @pytest.mark.parametrize("path", TEACHER_PAGES)
    def test_teacher_pages_render(self, teacher_context, path):
        response = teacher_context["client"].get(path)
        assert response.status_code == 200
        assert "data-chart" in response.text

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_admin_pages_render(self, admin_client, path):
        response = admin_client.get(path)
        assert response.status_code == 200
        assert "data-chart" in response.text

    def test_login_redirects_each_role_to_its_own_portal(self, db):
        from sqlalchemy import select

        from app.models import Role, User
        from app.portals import PORTAL_HOME
        from tests.conftest import _login

        for role in (Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT):
            email = db.scalars(
                select(User.email).where(User.role == role).order_by(User.id).limit(1)
            ).one()
            client = _login(email)
            landing = client.get("/")
            assert landing.headers["location"] == PORTAL_HOME[role]

    def test_pages_escape_student_names(self, admin_client, db):
        """A name containing markup must be rendered as text, never as HTML."""
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import Student

        with SessionLocal() as session:
            student = session.scalars(select(Student).order_by(Student.id).limit(1)).one()
            original = student.full_name
            student.full_name = "<script>alert(1)</script>"
            session.commit()
        try:
            response = admin_client.get(f"/admin/student/{student.id}")
            assert response.status_code == 200
            assert "<script>alert(1)</script>" not in response.text
            assert "&lt;script&gt;" in response.text
        finally:
            with SessionLocal() as session:
                record = session.get(Student, student.id)
                record.full_name = original
                session.commit()


class TestExports:
    @pytest.mark.parametrize("key", SCHOOL_KEYS)
    def test_admin_can_export_every_school_chart(self, admin_client, key):
        response = admin_client.get(f"/export/{key}.csv")
        assert response.status_code == 200, f"{key}: {response.text[:200]}"
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert len(response.text.splitlines()) >= 4

    @pytest.mark.parametrize("key", STUDENT_KEYS)
    def test_parent_can_export_every_student_chart(self, parent_context, key):
        response = parent_context["client"].get(
            f"/export/{key}.csv", params={"student_id": parent_context["child_id"]}
        )
        assert response.status_code == 200, f"{key}: {response.text[:200]}"

    def test_export_filename_is_derived_from_the_chart_key_only(self, admin_client):
        response = admin_client.get("/export/school.kpis.csv")
        disposition = response.headers["content-disposition"]
        assert "school-kpis-" in disposition
        assert ".." not in disposition and "/" not in disposition

    def test_unknown_export_is_a_404(self, admin_client):
        assert admin_client.get("/export/school.nope.csv").status_code == 404


class TestHealth:
    def test_healthz_is_public(self, anon_client):
        response = anon_client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
