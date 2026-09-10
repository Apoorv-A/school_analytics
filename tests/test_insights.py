"""Student insights and extended risk signals."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.analytics import charts, metrics, queries
from app.deps import AccessScope, _current_academic_year
from app.models import (
    AcademicYear,
    Assessment,
    AssessmentType,
    Remark,
    RemarkCategory,
    Role,
    Score,
    Section,
    Student,
    Subject,
    Tenant,
    Term,
    User,
)
from app.schemas import FilterParams


class TestStudentInsights:
    def test_insights_report_has_actions(self, admin_client, parent_context):
        child_id = parent_context["child_id"]
        payload = admin_client.get(
            "/api/charts/student.insights",
            params={"student_id": child_id},
        ).json()
        assert payload["kind"] == "insights"
        assert "level" in payload["risk"]
        assert isinstance(payload["risk"]["reasons"], list)
        assert len(payload["actions"]) >= 1

    def test_assess_risk_includes_recent_drop(self):
        risk = metrics.assess_risk(
            70.0, 0.0, 95.0, recent_assessment_drop=-6.0, concern_remark_count=0
        )
        assert risk.level == "high"
        assert any("Recent assessments" in reason for reason in risk.reasons)

    def test_assess_risk_includes_concern_remarks(self):
        risk = metrics.assess_risk(
            72.0, 0.5, 95.0, concern_remark_count=2
        )
        assert risk.is_at_risk
        assert any("concern remark" in reason for reason in risk.reasons)


class TestParentInsightHeadline:
    def test_watch_level_headline(self):
        risk = metrics.assess_risk(72.0, 0.5, 95.0, concern_remark_count=1)
        assert risk.level == "watch"
        headline = "Some subjects need attention" if risk.level == "watch" else "Progress update"
        assert headline == "Some subjects need attention"


class TestStandingRiskSignals:
    def test_concern_remarks_flag_at_risk_cohort(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        db.add(
            Remark(
                tenant_id=tenant.id,
                student_id=student.id,
                category=RemarkCategory.CONCERN,
                body="Needs follow-up at home.",
                created_at=datetime.now(UTC),
            )
        )
        db.commit()

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        standings = queries.student_standings(
            db, FilterParams(student_id=student.id), scope
        )
        standing = next(item for item in standings if item.student_id == student.id)
        assert standing.concern_remark_count == 1
        assert standing.risk.is_at_risk
        assert any("concern remark" in reason for reason in standing.risk.reasons)

    def test_student_overview_risk_matches_standings_with_concern_remark(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        db.add(
            Remark(
                tenant_id=tenant.id,
                student_id=student.id,
                category=RemarkCategory.CONCERN,
                body="Needs follow-up at home.",
                created_at=datetime.now(UTC),
            )
        )
        db.commit()

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        filters = FilterParams(student_id=student.id)
        standings = queries.student_standings(db, filters, scope)
        standing = next(item for item in standings if item.student_id == student.id)
        overview = queries.student_overview(db, student, filters, scope)

        assert overview["risk"].level == standing.risk.level
        assert overview["risk"].reasons == standing.risk.reasons

        kpis = charts.student_kpis(db, filters, scope)
        status_card = next(card for card in kpis["cards"] if card["label"] == "Status")
        assert status_card["value"] == standing.risk.label
        assert status_card["hint"] == (
            "; ".join(standing.risk.reasons)
            if standing.risk.reasons
            else "No concerns flagged"
        )

    def test_concern_remarks_honor_term_filter(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        terms = db.scalars(
            select(Term)
            .where(Term.tenant_id == tenant.id)
            .order_by(Term.id)
            .limit(2)
        ).all()
        assert len(terms) >= 2
        first_term, second_term = terms[0], terms[1]

        db.add_all(
            [
                Remark(
                    tenant_id=tenant.id,
                    student_id=student.id,
                    term_id=first_term.id,
                    category=RemarkCategory.CONCERN,
                    body="Term one concern.",
                    created_at=datetime.now(UTC),
                ),
                Remark(
                    tenant_id=tenant.id,
                    student_id=student.id,
                    term_id=second_term.id,
                    category=RemarkCategory.CONCERN,
                    body="Term two concern.",
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        db.commit()

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        standings = queries.student_standings(
            db,
            FilterParams(student_id=student.id, term_id=first_term.id),
            scope,
        )
        standing = next(item for item in standings if item.student_id == student.id)
        assert standing.concern_remark_count == 1
        assert any("selected term" in reason for reason in standing.risk.reasons)

    def test_concern_remarks_intersect_term_and_30_day_window(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        terms = db.scalars(
            select(Term)
            .where(Term.tenant_id == tenant.id)
            .order_by(Term.id)
            .limit(2)
        ).all()
        assert len(terms) >= 2
        first_term, second_term = terms[0], terms[1]
        now = datetime.now(UTC)
        old = now - timedelta(days=60)

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        term_filter = FilterParams(student_id=student.id, term_id=first_term.id)
        baseline = queries._concern_remark_counts(
            db, [student.id], tenant.id, filters=term_filter
        ).get(student.id, 0)

        db.add_all(
            [
                Remark(
                    tenant_id=tenant.id,
                    student_id=student.id,
                    term_id=first_term.id,
                    category=RemarkCategory.CONCERN,
                    body="Old term-one concern.",
                    created_at=old,
                ),
                Remark(
                    tenant_id=tenant.id,
                    student_id=student.id,
                    term_id=first_term.id,
                    category=RemarkCategory.CONCERN,
                    body="Recent term-one concern.",
                    created_at=now,
                ),
                Remark(
                    tenant_id=tenant.id,
                    student_id=student.id,
                    term_id=second_term.id,
                    category=RemarkCategory.CONCERN,
                    body="Recent term-two concern.",
                    created_at=now,
                ),
            ]
        )
        db.commit()

        standings = queries.student_standings(db, term_filter, scope)
        standing = next(item for item in standings if item.student_id == student.id)
        assert standing.concern_remark_count == baseline + 1


class TestAssessmentDrops:
    def test_recent_assessment_drops_avoids_fetch_facts(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        filters = FilterParams(student_id=student.id)

        with patch(
            "app.analytics.queries.fetch_facts",
            side_effect=AssertionError("fetch_facts must not run for batch drops"),
        ):
            drops = queries._recent_assessment_drops(db, [student.id], filters, scope)

        assert student.id in drops

    def test_recent_assessment_drops_computes_last_six(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        section = db.get(Section, student.section_id)
        subject = db.scalars(
            select(Subject).where(Subject.tenant_id == tenant.id).limit(1)
        ).one()
        year = db.get(AcademicYear, section.academic_year_id)
        term = db.scalars(
            select(Term).where(Term.academic_year_id == year.id).limit(1)
        ).one()
        prior_marks = [80.0, 80.0, 80.0]
        recent_marks = [50.0, 50.0, 50.0]
        for index, marks in enumerate([*prior_marks, *recent_marks], start=1):
            assessment = Assessment(
                tenant_id=tenant.id,
                name=f"Drop signal test {index}",
                assessment_type=AssessmentType.UNIT_TEST,
                subject_id=subject.id,
                section_id=section.id,
                term_id=term.id,
                max_marks=100.0,
                conducted_on=date(2099, 1, index),
            )
            db.add(assessment)
            db.flush()
            db.add(
                Score(
                    tenant_id=tenant.id,
                    student_id=student.id,
                    assessment_id=assessment.id,
                    marks_obtained=marks,
                    is_absent=False,
                )
            )
        db.commit()

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        drops = queries._recent_assessment_drops(
            db, [student.id], FilterParams(student_id=student.id), scope
        )
        assert drops[student.id] == -30.0

    def test_recent_assessment_drops_per_subject_not_mixed_timeline(self, db):
        """Interleaved subjects must not look like a slide when each is flat."""
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        section = db.get(Section, student.section_id)
        subjects = db.scalars(
            select(Subject).where(Subject.tenant_id == tenant.id).limit(2)
        ).all()
        assert len(subjects) == 2
        year = db.get(AcademicYear, section.academic_year_id)
        term = db.scalars(
            select(Term).where(Term.academic_year_id == year.id).limit(1)
        ).one()
        marks_by_subject = {subjects[0].id: 80.0, subjects[1].id: 60.0}
        day = 1
        for _ in range(6):
            for subject in subjects:
                assessment = Assessment(
                    tenant_id=tenant.id,
                    name=f"Mixed timeline test {subject.code} {day}",
                    assessment_type=AssessmentType.UNIT_TEST,
                    subject_id=subject.id,
                    section_id=section.id,
                    term_id=term.id,
                    max_marks=100.0,
                    conducted_on=date(2099, 2, day),
                )
                db.add(assessment)
                db.flush()
                db.add(
                    Score(
                        tenant_id=tenant.id,
                        student_id=student.id,
                        assessment_id=assessment.id,
                        marks_obtained=marks_by_subject[subject.id],
                        is_absent=False,
                    )
                )
                day += 1
        db.commit()

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        drops = queries._recent_assessment_drops(
            db,
            [student.id],
            FilterParams(
                student_id=student.id,
                subject_ids=[subjects[0].id, subjects[1].id],
            ),
            scope,
        )
        assert drops[student.id] == 0.0

    def test_recent_assessment_drops_honors_subject_filter(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        section = db.get(Section, student.section_id)
        subjects = db.scalars(
            select(Subject).where(Subject.tenant_id == tenant.id).limit(2)
        ).all()
        assert len(subjects) == 2
        year = db.get(AcademicYear, section.academic_year_id)
        term = db.scalars(
            select(Term).where(Term.academic_year_id == year.id).limit(1)
        ).one()
        for subject, prior_marks, recent_marks in (
            (subjects[0], [80.0, 80.0, 80.0], [50.0, 50.0, 50.0]),
            (subjects[1], [90.0, 90.0, 90.0], [90.0, 90.0, 90.0]),
        ):
            for index, marks in enumerate([*prior_marks, *recent_marks], start=1):
                assessment = Assessment(
                    tenant_id=tenant.id,
                    name=f"Filter test {subject.code} {index}",
                    assessment_type=AssessmentType.UNIT_TEST,
                    subject_id=subject.id,
                    section_id=section.id,
                    term_id=term.id,
                    max_marks=100.0,
                    conducted_on=date(2099, 3, index + (0 if subject == subjects[0] else 10)),
                )
                db.add(assessment)
                db.flush()
                db.add(
                    Score(
                        tenant_id=tenant.id,
                        student_id=student.id,
                        assessment_id=assessment.id,
                        marks_obtained=marks,
                        is_absent=False,
                    )
                )
        db.commit()

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        drops = queries._recent_assessment_drops(
            db,
            [student.id],
            FilterParams(student_id=student.id, subject_id=subjects[0].id),
            scope,
        )
        assert drops[student.id] == -30.0


class TestAttendanceFilters:
    def test_attendance_by_student_honors_section_ids(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        sections = db.scalars(
            select(Section.id).where(Section.tenant_id == tenant.id).limit(2)
        ).all()
        if len(sections) < 2:
            pytest.skip("need two sections")

        students_by_section = {
            section_id: set(
                db.scalars(
                    select(Student.id).where(
                        Student.tenant_id == tenant.id,
                        Student.section_id == section_id,
                        Student.is_active.is_(True),
                    )
                ).all()
            )
            for section_id in sections
        }
        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )

        all_attendance = queries.attendance_by_student(db, None, FilterParams(), scope)
        filtered = queries.attendance_by_student(
            db, None, FilterParams(section_ids=[sections[0]]), scope
        )

        assert filtered
        assert set(filtered).issubset(students_by_section[sections[0]])
        other_section_students = students_by_section[sections[1]] & set(all_attendance)
        if other_section_students:
            assert not set(filtered) & other_section_students

    def test_attendance_section_ids_win_over_section_id(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        sections = db.scalars(
            select(Section.id).where(Section.tenant_id == tenant.id).limit(2)
        ).all()
        if len(sections) < 2:
            pytest.skip("need two sections")

        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=_current_academic_year(db, tenant.id),
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        by_ids = queries.attendance_by_student(
            db,
            None,
            FilterParams(section_ids=[sections[0]], section_id=sections[1]),
            scope,
        )
        by_section_id = queries.attendance_by_student(
            db, None, FilterParams(section_id=sections[0]), scope
        )
        assert set(by_ids) == set(by_section_id)
