"""F01-02 formula regression: cohort KPIs must match documented oracle rules.

``METRIC_FORMULAS`` is the lightweight contract key set; canonical copy lives in
``app.analytics.metric_registry`` (F01-01). Oracles recompute from graded
``ScoreFact`` rows (same scope and filters as charts).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.analytics import charts, metrics, queries
from app.deps import AccessScope, _current_academic_year
from app.models import Grade, Role, Section, Tenant, Term, User
from app.schemas import FilterParams

# ---------------------------------------------------------------------------
# Oracle contract keys (aligned with F01-01 registry entries below)
# ---------------------------------------------------------------------------

METRIC_FORMULAS: dict[str, str] = {
    "cohort_average": (
        "Weighted mean of every graded result percentage: "
        "sum(pct * assessment.weightage) / sum(weightage); "
        "if sum(weightage) is zero, unweighted mean of percentages."
    ),
    "pass_rate": (
        "Share of graded result percentages at or above the tenant pass threshold "
        "(default 33%), one row per score."
    ),
    "median_score": (
        "Median of graded result percentages; even cohorts use the mean of the two "
        "middle values."
    ),
    "students_assessed": "Count of distinct students with at least one graded result in scope.",
    "assessments_held": "Count of distinct assessments with at least one graded result.",
    "attendance_rate": (
        "Unweighted mean of per-student attendance percentages (present days / "
        "recorded days per school policy), across students with attendance rows."
    ),
    "at_risk_count": (
        "Students in scope whose standing risk level is watch or high "
        "(``metrics.assess_risk`` on weighted average, term slope, attendance, "
        "failing subjects, recent drop, concern remarks)."
    ),
    "spread": "Sample standard deviation of graded result percentages (needs >= 2 values).",
}

# Registry metric ids exercised by cohort/chart oracles in this module.
REGISTRY_COHORT_METRIC_IDS = (
    "perf.cohort_weighted_average",
    "perf.assessment_pass_rate",
    "perf.median_assessment_pct",
    "perf.students_assessed",
    "perf.assessments_held",
    "attendance.student_rate",
    "risk.students_needing_support",
    "perf.score_spread",
)


@dataclass(frozen=True)
class CohortOracle:
    average: float | None
    pass_rate: float | None
    median: float | None
    students: int
    assessments: int
    subjects: int
    sections: int
    results: int
    attendance: float | None
    spread: float | None
    at_risk_count: int
    high_risk_count: int


def _sunrise_admin_scope(db) -> AccessScope:
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    admin = db.scalars(
        select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN).limit(1)
    ).one()
    return AccessScope(
        tenant_id=tenant.id,
        user=admin,
        academic_year=_current_academic_year(db, tenant.id),
        student_ids=None,
        section_ids=None,
        subject_ids=None,
        students=(),
    )


def _graded_facts(db, filters: FilterParams, scope: AccessScope) -> list[queries.ScoreFact]:
    return [
        fact
        for fact in queries.fetch_facts(db, filters, scope, graded_only=True)
        if fact.pct is not None and not fact.is_absent
    ]


def _oracle_weighted_average(facts: list[queries.ScoreFact]) -> float | None:
    if not facts:
        return None
    numerator = sum(f.pct * f.weightage for f in facts if f.pct is not None)
    denominator = sum(f.weightage for f in facts)
    if denominator > 0:
        return round(numerator / denominator, 2)
    return metrics.safe_mean([f.pct for f in facts if f.pct is not None])


def cohort_oracle(
    db, filters: FilterParams, scope: AccessScope
) -> CohortOracle:
    """Independent recomputation of ``queries.cohort_overview`` headline metrics."""
    facts = _graded_facts(db, filters, scope)
    values = [round(f.pct, 2) for f in facts if f.pct is not None]

    attendance = queries.attendance_by_student(db, None, filters, scope)
    attendance_values = [
        info["percentage"]
        for info in attendance.values()
        if info.get("percentage") is not None
    ]

    standings = queries.student_standings(db, filters, scope)
    at_risk = [s for s in standings if s.risk.is_at_risk]
    high_risk = [s for s in standings if s.risk.level == "high"]

    return CohortOracle(
        average=_oracle_weighted_average(facts),
        pass_rate=metrics.pass_rate(values),
        median=metrics.score_median(values),
        students=len({f.student_id for f in facts}),
        assessments=len({f.assessment_id for f in facts}),
        subjects=len({f.subject_id for f in facts}),
        sections=len({f.section_id for f in facts}),
        results=len(values),
        attendance=metrics.safe_mean(attendance_values),
        spread=metrics.consistency(values),
        at_risk_count=len(at_risk),
        high_risk_count=len(high_risk),
    )


def _overview_matches_oracle(overview: dict, oracle: CohortOracle) -> None:
    assert overview["average"] == oracle.average
    assert overview["pass_rate"] == oracle.pass_rate
    assert overview["median"] == oracle.median
    assert overview["students"] == oracle.students
    assert overview["assessments"] == oracle.assessments
    assert overview["subjects"] == oracle.subjects
    assert overview["sections"] == oracle.sections
    assert overview["results"] == oracle.results
    assert overview["attendance"] == oracle.attendance
    assert overview["spread"] == oracle.spread
    assert overview["at_risk_count"] == oracle.at_risk_count
    assert overview["high_risk_count"] == oracle.high_risk_count


def _parse_kpi_percent(value: str) -> float | None:
    if value in ("--", ""):
        return None
    return float(value.rstrip("%"))


@pytest.fixture
def admin_scope(db) -> AccessScope:
    return _sunrise_admin_scope(db)


class TestDocumentedPureFormulas:
    """Pin pure helpers that cohort SQL delegates to."""

    def test_score_median_even_length_uses_midpair_mean(self):
        assert metrics.score_median([40.0, 60.0]) == 50.0
        assert metrics.score_median([10.0, 20.0, 30.0, 40.0]) == 25.0

    def test_score_median_odd_length(self):
        assert metrics.score_median([10.0, 20.0, 30.0]) == 20.0

    def test_metric_formula_inventory_is_non_empty(self):
        """F01-03 should migrate these strings into the versioned registry."""
        assert set(METRIC_FORMULAS) >= {
            "cohort_average",
            "pass_rate",
            "median_score",
            "students_assessed",
            "attendance_rate",
            "at_risk_count",
        }


class TestCohortOverviewFormulas:
    def test_full_year_overview_matches_oracle(self, db, admin_scope):
        filters = FilterParams()
        overview = queries.cohort_overview(db, filters, admin_scope)
        oracle = cohort_oracle(db, filters, admin_scope)
        _overview_matches_oracle(overview, oracle)

    def test_single_term_overview_matches_oracle(self, db, admin_scope):
        term_id = db.scalars(select(Term.id).order_by(Term.sequence).limit(1)).one()
        filters = FilterParams(term_id=term_id)
        overview = queries.cohort_overview(db, filters, admin_scope)
        oracle = cohort_oracle(db, filters, admin_scope)
        _overview_matches_oracle(overview, oracle)

    def test_grade_filter_overview_matches_oracle(self, db, admin_scope):
        grade_id = db.scalars(select(Grade.id).order_by(Grade.level).limit(1)).one()
        filters = FilterParams(grade_id=grade_id)
        overview = queries.cohort_overview(db, filters, admin_scope)
        oracle = cohort_oracle(db, filters, admin_scope)
        _overview_matches_oracle(overview, oracle)

    def test_section_filter_overview_matches_oracle(self, db, admin_scope):
        section_id = db.scalars(select(Section.id).order_by(Section.id).limit(1)).one()
        filters = FilterParams(section_id=section_id)
        overview = queries.cohort_overview(db, filters, admin_scope)
        oracle = cohort_oracle(db, filters, admin_scope)
        _overview_matches_oracle(overview, oracle)


class TestKpiChartPayloads:
    """Chart handlers must surface the same numbers as ``cohort_overview``."""

    def test_school_kpis_match_cohort_overview(self, db, admin_scope, admin_client):
        filters = FilterParams()
        overview = queries.cohort_overview(db, filters, admin_scope)
        payload = admin_client.get("/api/charts/school.kpis").json()

        by_label = {card["label"]: card for card in payload["cards"]}
        # KPI cards render percentages to one decimal via ``charts._fmt``.
        assert _parse_kpi_percent(by_label["School average"]["value"]) == round(
            overview["average"], 1
        )
        assert _parse_kpi_percent(by_label["Pass rate"]["value"]) == overview["pass_rate"]
        assert int(by_label["Students assessed"]["value"]) == overview["students"]
        assert int(by_label["Assessments held"]["value"]) == overview["assessments"]
        assert _parse_kpi_percent(by_label["Attendance"]["value"]) == round(
            overview["attendance"], 1
        )
        assert int(by_label["Students needing support"]["value"]) == overview[
            "at_risk_count"
        ]
        assert by_label["Pass rate"]["hint"] == (
            f"{overview['results']:,} graded results"
        )

    def test_section_kpis_match_cohort_overview(self, db, admin_scope):
        section_id = db.scalars(select(Section.id).order_by(Section.id).limit(1)).one()
        filters = FilterParams(section_id=section_id)
        overview = queries.cohort_overview(db, filters, admin_scope)
        payload = charts.section_kpis(db, filters, admin_scope)

        by_label = {card["label"]: card for card in payload["cards"]}
        assert _parse_kpi_percent(by_label["Class average"]["value"]) == round(
            overview["average"], 1
        )
        assert _parse_kpi_percent(by_label["Pass rate"]["value"]) == overview["pass_rate"]
        assert int(by_label["Students"]["value"]) == overview["students"]
        spread_display = float(by_label["Spread"]["value"].replace(" pts", ""))
        assert spread_display == pytest.approx(overview["spread"], abs=0.06)
        assert _parse_kpi_percent(by_label["Attendance"]["value"]) == round(
            overview["attendance"], 1
        )
        assert int(by_label["Needs attention"]["value"]) == overview["at_risk_count"]
        assert by_label["Pass rate"]["hint"] == (
            f"{overview['results']:,} graded results"
        )

    def test_score_values_align_with_oracle_result_count(self, db, admin_scope):
        filters = FilterParams()
        values = queries.score_values(db, filters, admin_scope)
        oracle = cohort_oracle(db, filters, admin_scope)
        assert len(values) == oracle.results


class TestMetricRegistryAlignment:
    """F01-01 registry must stay valid and document formulas under test."""

    def test_validate_registry(self):
        from app.analytics.metric_registry import validate_registry

        assert validate_registry() == []

    def test_cohort_metrics_registered_with_formula_text(self):
        from app.analytics.metric_registry import get_metric

        for metric_id in REGISTRY_COHORT_METRIC_IDS:
            entry = get_metric(metric_id)
            assert entry.numerator_denominator.strip()
            assert entry.summary.strip()
