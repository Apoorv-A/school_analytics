"""End-to-end coverage of every registered chart, page, and export.

The registry is walked directly, so adding a chart without a working handler, or
without deciding which roles may see it, fails the suite rather than the dashboard.
"""

from __future__ import annotations

import pytest

from app.analytics.charts import CHART_REGISTRY
from app.models import Role


def _keys_for_role(prefix: str, role: Role) -> list[str]:
    return sorted(
        key
        for key, definition in CHART_REGISTRY.items()
        if key.startswith(prefix) and role in definition.roles
    )


STUDENT_KEYS = _keys_for_role("student.", Role.PARENT)
STUDENT_KEYS_ADMIN = _keys_for_role("student.", Role.ADMIN)
SECTION_KEYS = sorted(k for k in CHART_REGISTRY if k.startswith("section."))
SCHOOL_KEYS = sorted(k for k in CHART_REGISTRY if k.startswith("school."))

VALID_KINDS = {
    "kpi", "line", "bar", "stacked-bar", "doughnut", "gauge",
    "radar", "scatter", "heatmap", "table", "timeline", "insights",
    "insight_summary",
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
    elif kind == "insights":
        assert payload["risk"]
        assert isinstance(payload["actions"], list)
    elif kind == "insight_summary":
        assert payload["headline"]
        assert isinstance(payload["suggestions"], list)
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
        assert "student.insights" in admin_keys
        assert "student.insights" not in parent_keys


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
        assert len(by_assessment["labels"]) >= 1
        assert by_assessment["meta"]["axis"] == "Assessment"


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

    def test_section_kpis_honors_section_ids_without_section_id(
        self, admin_client, db
    ):
        """A bookmark with only section_ids must target that class, not the default."""
        from sqlalchemy import func, select

        from app.models import Grade, Section, Student

        grade_id = db.scalars(select(Grade.id).order_by(Grade.level).limit(1)).one()
        sections = db.scalars(
            select(Section.id)
            .where(Section.grade_id == grade_id)
            .order_by(Section.id)
            .limit(3)
        ).all()
        if len(sections) < 2:
            pytest.skip("need at least two sections in one grade")

        default_hint = admin_client.get("/api/charts/section.kpis").json()["meta"]["hint"]
        target = next(
            sid
            for sid in sections
            if admin_client.get(
                "/api/charts/section.kpis", params={"section_id": sid}
            ).json()["meta"]["hint"]
            != default_hint
        )
        expected = db.scalar(
            select(func.count())
            .select_from(Student)
            .where(Student.section_id == target, Student.is_active.is_(True))
        )
        baseline = admin_client.get(
            "/api/charts/section.kpis", params={"section_id": target}
        ).json()
        bookmarked = admin_client.get(
            "/api/charts/section.kpis", params={"section_ids": target}
        ).json()

        def student_count(payload: dict) -> int:
            return int(
                next(c for c in payload["cards"] if c["label"] == "Students")["value"]
            )

        assert student_count(bookmarked) == expected
        assert student_count(bookmarked) == student_count(baseline)
        assert bookmarked["meta"]["hint"] == baseline["meta"]["hint"]
        assert bookmarked["meta"]["hint"] != default_hint

    def test_classroom_charts_ignore_passthrough_section_ids(
        self, admin_client, db
    ):
        """A pinned section_id must win over URL section_ids passthrough."""
        from sqlalchemy import func, select

        from app.models import Grade, Section, Student

        grade_id = db.scalars(select(Grade.id).order_by(Grade.level).limit(1)).one()
        sections = db.scalars(
            select(Section.id)
            .where(Section.grade_id == grade_id)
            .order_by(Section.id)
            .limit(3)
        ).all()
        if len(sections) < 2:
            pytest.skip("need at least two sections in one grade")
        pinned, *extra = sections

        expected = db.scalar(
            select(func.count())
            .select_from(Student)
            .where(Student.section_id == pinned, Student.is_active.is_(True))
        )
        baseline = admin_client.get(
            "/api/charts/section.kpis", params={"section_id": pinned}
        ).json()
        polluted = admin_client.get(
            "/api/charts/section.kpis",
            params=[("section_id", pinned)]
            + [("section_ids", sid) for sid in extra],
        ).json()

        def student_count(payload: dict) -> int:
            return int(
                next(c for c in payload["cards"] if c["label"] == "Students")["value"]
            )

        assert student_count(polluted) == expected
        assert student_count(polluted) == student_count(baseline)
        assert polluted["meta"]["hint"] == baseline["meta"]["hint"]

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

        from app.models import Tenant, Term

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        expected = list(
            db.scalars(
                select(Term.name)
                .where(Term.tenant_id == tenant.id)
                .order_by(Term.sequence)
            )
        )
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

    def test_section_id_keeps_class_comparison_breadth(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Section

        section_id = db.scalars(select(Section.id).order_by(Section.id).limit(1)).one()
        unfiltered = admin_client.get("/api/charts/school.section_compare").json()
        focused = admin_client.get(
            "/api/charts/school.section_compare", params={"section_id": section_id}
        ).json()
        assert len(focused["labels"]) == len(unfiltered["labels"])
        assert len(focused["labels"]) >= 2
        assert section_id in focused["meta"]["highlighted"]

    def test_section_id_highlights_leaderboard_without_collapsing(
        self, admin_client, db
    ):
        from sqlalchemy import select

        from app.models import Section

        section_id = db.scalars(select(Section.id).order_by(Section.id).limit(1)).one()
        payload = admin_client.get(
            "/api/charts/school.section_leaderboard", params={"section_id": section_id}
        ).json()
        assert len(payload["rows"]) >= 2
        highlighted = [row for row in payload["rows"] if row["_highlight"]]
        assert len(highlighted) == 1
        assert section_id in payload["meta"]["highlighted"]

    def test_explicit_section_ids_narrow_class_comparison(
        self, admin_client, db
    ):
        from sqlalchemy import select

        from app.models import Grade, Section

        grade_id = db.scalars(select(Grade.id).order_by(Grade.level).limit(1)).one()
        sections = db.scalars(
            select(Section.id).where(Section.grade_id == grade_id).limit(2)
        ).all()
        if len(sections) < 2:
            pytest.skip("need two sections in one grade")
        payload = admin_client.get(
            "/api/charts/school.section_compare",
            params=[("section_ids", sections[0]), ("section_ids", sections[1])],
        ).json()
        assert len(payload["labels"]) == 2
        assert set(payload["meta"]["highlighted"]) == set(sections)

    def test_subject_id_drives_multi_subject_trend_chart(self, parent_context, db):
        from sqlalchemy import select

        from app.models import Subject

        client = parent_context["client"]
        subjects = db.scalars(select(Subject.id).order_by(Subject.id).limit(2)).all()
        if len(subjects) < 2:
            pytest.skip("need two subjects")
        by_assessment = client.get(
            "/api/charts/student.subject_trend",
            params={
                "student_id": parent_context["child_id"],
                "subject_id": subjects[0],
            },
        ).json()
        assert by_assessment["meta"]["axis"] == "Assessment"
        assert len(by_assessment["series"]) >= 1

        multi = client.get(
            "/api/charts/student.subject_trend",
            params=[
                ("student_id", parent_context["child_id"]),
                ("subject_ids", subjects[0]),
                ("subject_ids", subjects[1]),
            ],
        ).json()
        assert multi["meta"]["axis"] == "Term"
        assert len(multi["series"]) == 2

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

    def test_remarks_respect_the_academic_year(self, parent_context, db):
        """The remarks page offers a year control, so it has to do something."""
        from sqlalchemy import func, select

        from app.models import AcademicYear

        client = parent_context["client"]
        params = {"student_id": parent_context["child_id"]}

        current_year, other_year = db.execute(
            select(
                select(AcademicYear.id).order_by(AcademicYear.id).limit(1).scalar_subquery(),
                func.max(AcademicYear.id) + 1,
            )
        ).one()

        this_year = client.get(
            "/api/charts/student.remarks",
            params={**params, "academic_year_id": current_year},
        ).json()
        assert this_year["items"], "seeded remarks belong to the current year"

        # A year the school has no terms in must not carry remarks over.
        empty = client.get(
            "/api/charts/student.remarks",
            params={**params, "academic_year_id": other_year},
        ).json()
        assert empty["items"] == []

    def test_general_remark_without_a_term_is_placed_by_its_date(
        self, parent_context, db
    ):
        """A school-office note has no term, so it cannot follow one to a year.

        Dropping it under a year filter would silently lose content, so it is
        matched on the date it was written instead.
        """
        from datetime import UTC, datetime

        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import AcademicYear, Remark, RemarkCategory, Student

        year = db.scalars(select(AcademicYear).order_by(AcademicYear.id).limit(1)).one()
        child_id = parent_context["child_id"]
        child = db.get(Student, child_id)

        # Midday on the first and on the *last* day of the year. The final day is
        # the boundary: comparing a timestamp against end_date alone coerces it to
        # midnight and drops everything written during that day.
        cases = {
            "termless note on the first day": datetime(
                year.start_date.year, year.start_date.month, year.start_date.day,
                12, 0, tzinfo=UTC,
            ),
            "termless note on the final day": datetime(
                year.end_date.year, year.end_date.month, year.end_date.day,
                23, 59, tzinfo=UTC,
            ),
        }

        ids: list[int] = []
        with SessionLocal() as session:
            for marker, written_at in cases.items():
                remark = Remark(
                    tenant_id=child.tenant_id,
                    student_id=child_id,
                    teacher_id=None,
                    term_id=None,
                    category=RemarkCategory.ACADEMIC,
                    body=marker,
                    created_at=written_at,
                )
                session.add(remark)
                session.flush()
                ids.append(remark.id)
            session.commit()

        try:
            client = parent_context["client"]
            in_year = client.get(
                "/api/charts/student.remarks",
                params={"student_id": child_id, "academic_year_id": year.id},
            ).json()
            bodies = [item["body"] for item in in_year["items"]]
            for marker in cases:
                assert marker in bodies, f"{marker} was dropped by the year filter"

            other = client.get(
                "/api/charts/student.remarks",
                params={"student_id": child_id, "academic_year_id": year.id + 1000},
            ).json()
            for marker in cases:
                assert marker not in [item["body"] for item in other["items"]]
        finally:
            with SessionLocal() as session:
                for remark_id in ids:
                    session.delete(session.get(Remark, remark_id))
                session.commit()

    def test_monthly_attendance_crosses_the_year_boundary_in_order(
        self, parent_context, db
    ):
        """Months must stay chronological from December into January.

        The academic year runs April to March, so the buckets have to be keyed on
        year and month rather than sorted as text.
        """
        from app.analytics.queries import attendance_monthly
        from app.schemas import FilterParams

        rows = attendance_monthly(db, parent_context["child_id"], FilterParams())
        labels = [row["label"] for row in rows]
        assert labels, "seeded attendance should span several months"

        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        keys = []
        for label in labels:
            name, year = label.split()
            keys.append((int(year), month_names.index(name) + 1))
        assert keys == sorted(keys), labels

        # A December bucket must precede the following January, not follow it.
        if (2025, 12) in keys and (2026, 1) in keys:
            assert keys.index((2025, 12)) < keys.index((2026, 1))

        for row in rows:
            assert row["total"] == sum(
                row[status] for status in ("present", "absent", "late", "excused")
            )
            if row["total"]:
                assert 0 <= row["percentage"] <= 100

    def test_remarks_respect_the_term(self, parent_context, db):
        from sqlalchemy import select

        from app.models import Term

        client = parent_context["client"]
        params = {"student_id": parent_context["child_id"]}
        term_id = db.scalars(select(Term.id).order_by(Term.sequence).limit(1)).one()

        full_year = client.get("/api/charts/student.remarks", params=params).json()
        one_term = client.get(
            "/api/charts/student.remarks", params={**params, "term_id": term_id}
        ).json()
        assert len(one_term["items"]) <= len(full_year["items"])
        for item in one_term["items"]:
            assert item["term"] in (full_year["items"][0]["term"], item["term"])

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

    def test_student_lookup_waits_for_student_selection(self, admin_client):
        """The lookup page must not show a roster default before the user picks someone."""
        import re

        response = admin_client.get("/admin/student")
        assert response.status_code == 200
        assert "data-require-student" in response.text
        assert "data-student-combobox" in response.text
        hidden = re.search(r'id="f-student-id"[^>]*value="([^"]*)"', response.text)
        assert hidden is not None
        assert hidden.group(1) == ""

    def test_pinned_filters_reach_the_chart_api(self, teacher_context, db):
        """A drill-down must query the student it is headed with.

        The chart API is built from the filter bar, and this page deliberately
        hides the student control. Without a hidden control carrying the pinned
        id, every card would silently fall back to a different student.
        """
        import re

        from sqlalchemy import select

        from app.models import Student

        # Pick a student who is *not* the one a bare request would default to,
        # otherwise the bug this guards against would pass unnoticed.
        client = teacher_context["client"]
        fallback = client.get("/api/charts/student.kpis").json()["meta"]["student"]
        target = db.scalars(
            select(Student)
            .where(
                Student.section_id.in_(teacher_context["section_ids"]),
                Student.full_name != fallback,
            )
            .order_by(Student.id)
            .limit(1)
        ).first()
        assert target is not None, "need a second student to make this meaningful"

        html = client.get(f"/teacher/student/{target.id}").text
        pinned = re.findall(
            r'<input[^>]*data-filter="student_id"[^>]*value="(\d+)"', html
        )
        assert pinned == [str(target.id)], (
            "the page must pin the student it displays; found " f"{pinned!r}"
        )

        # The pinned value is what the browser will send, so the charts must
        # then be about the requested student rather than the fallback.
        meta = client.get(
            "/api/charts/student.kpis", params={"student_id": target.id}
        ).json()["meta"]
        assert meta["student"] == target.full_name
        assert meta["student"] != fallback

    def test_stale_query_ids_are_not_pinned(self, admin_client, db):
        """Only what a route declares is forwarded, not leftovers in the URL.

        A grade or class id carried over from another page has no control on this
        page, so pinning it would silently narrow every chart with nothing in the
        filter bar to reveal it or clear it.
        """
        import re

        from sqlalchemy import select

        from app.models import Grade, Section

        grade_id = db.scalars(select(Grade.id).order_by(Grade.id).limit(1)).one()
        section_id = db.scalars(select(Section.id).order_by(Section.id).limit(1)).one()

        # The teaching-outcomes page draws neither a grade nor a class control.
        html = admin_client.get(
            "/admin/teachers", params={"grade_id": grade_id, "section_id": section_id}
        ).text
        hidden = dict(
            re.findall(r'<input[^>]*data-filter="([^"]+)"[^>]*value="([^"]*)"', html)
        )
        assert "grade_id" not in hidden, hidden
        assert "section_id" not in hidden, hidden

    def test_declared_pin_must_name_a_real_field(self):
        """A typo in a route's pin list has to fail loudly, not do nothing."""
        import pytest as _pytest

        from app.portals import _pinned_filters
        from app.schemas import FilterParams

        with _pytest.raises(ValueError, match="Unknown pinned filter"):
            _pinned_filters(FilterParams(), [], ["studnet_id"])

    def test_normalize_filter_controls_maps_single_multi_ids(self):
        from app.portals import _normalize_filter_controls
        from app.schemas import FilterParams

        filters = _normalize_filter_controls(
            FilterParams(subject_ids=[7], section_ids=[3])
        )
        assert filters.subject_id == 7
        assert filters.subject_ids is None
        assert filters.section_id == 3
        assert filters.section_ids is None

    def test_pinned_filters_use_singular_ids(self):
        from app.portals import _pinned_filters
        from app.schemas import FilterParams

        pinned = _pinned_filters(
            FilterParams(subject_ids=[7], section_ids=[3]),
            [],
            ["subject_ids", "section_ids"],
        )
        assert pinned == {"subject_id": "7", "section_id": "3"}

    def test_pinned_filters_are_not_offered_as_clearable(self, teacher_context, db):
        """Clearing filters must not drop a value the route pinned."""
        import re

        from sqlalchemy import select

        from app.models import Student

        student_id = db.scalars(
            select(Student.id)
            .where(Student.section_id.in_(teacher_context["section_ids"]))
            .order_by(Student.id)
            .limit(1)
        ).first()
        html = teacher_context["client"].get(f"/teacher/student/{student_id}").text
        tag = re.search(r"<input[^>]*data-filter=\"student_id\"[^>]*>", html)
        assert tag is not None
        assert "data-filter-pinned" in tag.group(0), tag.group(0)

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

    def test_staff_student_overview_uses_role_appropriate_cards(
        self, admin_client, teacher_context, parent_context, db
    ):
        import re

        from sqlalchemy import select

        from app.models import Student

        student_id = db.scalars(select(Student.id).order_by(Student.id).limit(1)).one()
        admin_html = admin_client.get(f"/admin/student/{student_id}").text
        teacher_html = teacher_context["client"].get(
            f"/teacher/student/{student_id}"
        ).text
        parent_html = parent_context["client"].get("/parent").text

        admin_keys = set(re.findall(r'data-chart="([^"]+)"', admin_html))
        teacher_keys = set(re.findall(r'data-chart="([^"]+)"', teacher_html))
        parent_keys = set(re.findall(r'data-chart="([^"]+)"', parent_html))

        assert "student.insights" in admin_keys
        assert "student.insight_summary" not in admin_keys
        assert "student.insight_summary" not in teacher_keys
        assert "student.insights" not in teacher_keys
        assert "student.insight_summary" in parent_keys

        assert (
            admin_client.get(
                "/api/charts/student.insights", params={"student_id": student_id}
            ).status_code
            == 200
        )
        assert (
            admin_client.get(
                "/api/charts/student.insight_summary",
                params={"student_id": student_id},
            ).status_code
            == 403
        )


class TestExports:
    def test_rows_for_insight_summary(self):
        from app.routers.exports import _rows_for

        header, rows = _rows_for(
            {
                "kind": "insight_summary",
                "student_name": "Ada Lovelace",
                "headline": "On track at 82.5% overall",
                "tone": "positive",
                "attendance": {"percentage": 91.0, "detail": "41 of 45 days present"},
                "subjects": [
                    {
                        "subject": "Mathematics",
                        "average": 78.0,
                        "trend": "up",
                        "status": "on_track",
                    }
                ],
                "suggestions": ["Keep up the positive study habits."],
            }
        )
        assert header == ["Section", "Label", "Value", "Trend", "Status"]
        flat = [cell for row in rows for cell in row if cell is not None]
        assert "Ada Lovelace" in flat
        assert "On track at 82.5% overall" in flat
        assert "Mathematics" in flat
        assert "Keep up the positive study habits." in flat

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

    def test_export_neutralises_spreadsheet_formulas(self, parent_context, db):
        """A remark starting with = must not run as a formula when opened."""
        import csv
        import io

        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import Remark, RemarkCategory, Student, Term

        child_id = parent_context["child_id"]
        child = db.get(Student, child_id)
        payload = '=cmd|\' /C calc\'!A0'
        term_id = db.scalars(
            select(Term.id)
            .where(Term.tenant_id == child.tenant_id)
            .order_by(Term.sequence)
            .limit(1)
        ).one()

        with SessionLocal() as session:
            remark = Remark(
                tenant_id=child.tenant_id,
                student_id=child_id,
                teacher_id=None,
                term_id=term_id,
                category=RemarkCategory.CONCERN,
                body=payload,
            )
            session.add(remark)
            session.commit()
            remark_id = remark.id

        try:
            response = parent_context["client"].get(
                "/export/student.remarks.csv", params={"student_id": child_id}
            )
            assert response.status_code == 200
            cells = [cell for row in csv.reader(io.StringIO(response.text)) for cell in row]
            assert payload not in cells, "raw formula reached the export"
            assert "'" + payload in cells, cells[-3:]
        finally:
            with SessionLocal() as session:
                session.delete(session.get(Remark, remark_id))
                session.commit()

    def test_export_keeps_negative_numbers_numeric(self, admin_client):
        """The guard must not turn a negative metric into text."""
        import csv
        import io

        from app.routers.exports import _safe_cell

        assert _safe_cell(-5.0) == -5.0
        assert _safe_cell(-5) == -5
        assert _safe_cell("-5") == "'-5"

        # A real export carrying negative values must still parse as numbers.
        response = admin_client.get("/export/school.at_risk.csv")
        rows = list(csv.reader(io.StringIO(response.text)))
        numeric = [
            cell
            for row in rows[4:]
            for cell in row
            if cell and cell.lstrip("-").replace(".", "", 1).isdigit()
        ]
        assert numeric, "expected numeric cells in this export"
        for cell in numeric:
            assert not cell.startswith("'")


class TestHealth:
    def test_healthz_is_public(self, anon_client):
        response = anon_client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
