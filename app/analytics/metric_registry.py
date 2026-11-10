"""Canonical metric and scorecard inventory (F01-01).

Stable metric IDs, human labels, role audiences, chart associations, and pointers to
the code that implements each formula. F01-02 (formula regression tests) and F01-03
(glossary HTTP API / appendix) should import from this module rather than duplicating
definitions.

Contract for downstream tasks
-----------------------------
F01-02:
    from app.analytics.metric_registry import (
        METRICS,
        REGISTRY_VERSION,
        get_metric,
        iter_metrics,
        metric_ids,
        validate_registry,
    )

F01-03:
    from app.analytics.metric_registry import (
        REGISTRY_VERSION,
        get_metric,
        metric_visible,
        search_metrics_for_role,
        serialize_metric_for_role,
    )
    # ``app/routers/metrics_glossary.py`` serves ``/api/metrics`` and ``/help/metrics``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.analytics.charts import CHART_REGISTRY
from app.models import Role

REGISTRY_VERSION = "1.0.0"

ALL_ROLES = frozenset({Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT})
STAFF = frozenset({Role.ADMIN, Role.TEACHER})
ADMIN_ONLY = frozenset({Role.ADMIN})
FAMILY = frozenset({Role.PARENT, Role.STUDENT})


class MetricKind(StrEnum):
    KPI = "kpi"
    DERIVED = "derived"
    AGGREGATE = "aggregate"
    CLASSIFICATION = "classification"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True)
class MetricDef:
    """One authoritative metric definition."""

    id: str
    label: str
    kind: MetricKind
    audience: frozenset[Role]
    chart_keys: tuple[str, ...]
    summary: str
    numerator_denominator: str
    inclusion: str
    exclusion: str
    filter_semantics: str
    reporting_period: str
    data_source: str
    implementation: tuple[str, ...]
    version: str = REGISTRY_VERSION
    family_safe: bool = True
    privacy_note: str | None = None
    kpi_card_labels: tuple[str, ...] = ()
    known_limitations: str | None = None


def _roles(role_set: frozenset[Role]) -> list[str]:
    return sorted(r.value for r in role_set)


# --------------------------------------------------------------------------- #
# Metric definitions (verify against queries.py / metrics.py before changing)
# --------------------------------------------------------------------------- #

METRICS: dict[str, MetricDef] = {}

_METRIC_LIST: list[MetricDef] = [
    MetricDef(
        id="perf.cohort_weighted_average",
        label="School / class weighted average",
        kind=MetricKind.KPI,
        audience=ALL_ROLES,
        chart_keys=(
            "school.kpis",
            "section.kpis",
            "school.grade_averages",
            "school.subject_averages",
            "school.section_compare",
            "school.section_leaderboard",
            "section.subject_averages",
        ),
        kpi_card_labels=("School average", "Class average"),
        summary=(
            "Mean assessment percentage weighted by each assessment's weightage "
            "across every graded result in the active filter scope."
        ),
        numerator_denominator="sum(pct * weightage) / sum(weightage) per SQL aggregate",
        inclusion="Graded Score rows matching filters and access scope",
        exclusion="Absent marks (no percentage); rows outside tenant or scope",
        filter_semantics=(
            "Honours academic year, term, grade, section, subject, and date filters "
            "via ``FilterParams`` and ``AccessScope`` on ``_base_select``."
        ),
        reporting_period="Selected term or academic year when filtered; otherwise in-scope history",
        data_source="``scores`` joined to ``assessments`` (ERP import or school entry)",
        implementation=(
            "app.analytics.queries:WEIGHTED_AVG_EXPR",
            "app.analytics.queries:cohort_overview",
            "app.analytics.metrics:weighted_average",
        ),
        known_limitations=(
            "Student-level overall average on ``student.kpis`` uses an unweighted mean "
            "of subject averages, not this SQL weighting."
        ),
    ),
    MetricDef(
        id="perf.student_overall_average",
        label="Student overall average",
        kind=MetricKind.KPI,
        audience=ALL_ROLES,
        chart_keys=("student.kpis", "student.subject_table"),
        kpi_card_labels=("Overall average",),
        summary="Arithmetic mean of the student's per-subject averages in filter scope.",
        numerator_denominator="mean(subject_averages)",
        inclusion="Subjects with at least one graded result for the student",
        exclusion="Subjects with no assessments in scope",
        filter_semantics="Same subject/term/year filters as ``student_subject_summaries``.",
        reporting_period="Filtered term/year or all in-scope terms",
        data_source="Per-subject weighted averages from ``student_subject_summaries``",
        implementation=(
            "app.analytics.queries:student_overview",
            "app.analytics.metrics:safe_mean",
        ),
    ),
    MetricDef(
        id="perf.median_assessment_pct",
        label="Median assessment percentage",
        kind=MetricKind.DERIVED,
        audience=STAFF | ADMIN_ONLY,
        chart_keys=("school.kpis", "section.kpis"),
        kpi_card_labels=(),
        summary="Median of individual assessment result percentages in scope.",
        numerator_denominator="median(score_values)",
        inclusion="Each non-absent result percentage once",
        exclusion="Absent attempts",
        filter_semantics="Same as ``score_values``.",
        reporting_period="Current filter window",
        data_source="``scores`` / ``assessments``",
        implementation=(
            "app.analytics.queries:cohort_overview",
            "app.analytics.queries:score_values",
        ),
        family_safe=False,
        known_limitations=(
            "Even-count cohorts use the upper middle index (``sorted[n//2]``), not the "
            "statistical median of two middles — F01-02 should regression-test."
        ),
    ),
    MetricDef(
        id="perf.assessment_pass_rate",
        label="Assessment pass rate",
        kind=MetricKind.KPI,
        audience=ALL_ROLES,
        chart_keys=(
            "school.kpis",
            "section.kpis",
            "school.pass_rate",
            "school.grade_averages",
            "school.subject_averages",
            "section.subject_averages",
            "school.section_leaderboard",
            "school.teacher_effectiveness",
        ),
        kpi_card_labels=("Pass rate",),
        summary="Share of assessment results at or above the tenant pass percentage.",
        numerator_denominator="count(pct ≥ pass_threshold) / count(results)",
        inclusion="Graded percentages in scope",
        exclusion="Absent results",
        filter_semantics="Tenant ``pass_percentage`` from ``TenantSettings``.",
        reporting_period="Filter window",
        data_source="``scores``",
        implementation=("app.analytics.metrics:pass_rate", "app.analytics.queries:score_values"),
        privacy_note="Rate is over results, not unique students passing a year.",
    ),
    MetricDef(
        id="perf.students_assessed",
        label="Students assessed",
        kind=MetricKind.KPI,
        audience=ADMIN_ONLY,
        chart_keys=("school.kpis",),
        kpi_card_labels=("Students assessed",),
        summary="Count of distinct students with at least one graded result in scope.",
        numerator_denominator="count(distinct student_id)",
        inclusion="Students with any in-scope score",
        exclusion="Students with only absences",
        filter_semantics="Cohort filters on ``_base_select``.",
        reporting_period="Filter window",
        data_source="``scores``",
        implementation=("app.analytics.queries:cohort_overview",),
        family_safe=False,
    ),
    MetricDef(
        id="perf.assessment_results_count",
        label="Assessment results",
        kind=MetricKind.AGGREGATE,
        audience=STAFF | ADMIN_ONLY,
        chart_keys=("school.kpis", "section.kpis", "school.distribution", "section.distribution"),
        kpi_card_labels=(),
        summary="Count of individual graded result rows in scope.",
        numerator_denominator="count(score.id)",
        inclusion="All graded rows",
        exclusion="Ungraded/absent rows excluded from percentage lists",
        filter_semantics="Standard analytics filters",
        reporting_period="Filter window",
        data_source="``scores``",
        implementation=(
            "app.analytics.queries:cohort_overview",
            "app.analytics.queries:score_values",
        ),
        family_safe=False,
    ),
    MetricDef(
        id="perf.assessments_held",
        label="Assessments held",
        kind=MetricKind.KPI,
        audience=ADMIN_ONLY,
        chart_keys=("school.kpis",),
        kpi_card_labels=("Assessments held",),
        summary="Distinct assessments conducted in the filtered scope.",
        numerator_denominator="count(distinct assessment.id)",
        inclusion="Assessments with at least one linked score in scope",
        exclusion="—",
        filter_semantics="Standard filters",
        reporting_period="Filter window",
        data_source="``assessments`` / ``scores``",
        implementation=("app.analytics.queries:cohort_overview",),
        family_safe=False,
    ),
    MetricDef(
        id="perf.gap_to_class",
        label="Versus class average",
        kind=MetricKind.KPI,
        audience=ALL_ROLES,
        chart_keys=("student.kpis", "student.subject_table"),
        kpi_card_labels=("Versus class average",),
        summary="Student overall average minus class mean of subject averages (points).",
        numerator_denominator="student_overall - class_overall",
        inclusion="Both averages computed on the same filter set",
        exclusion="Either average missing → no gap",
        filter_semantics="Student and class summaries share filters",
        reporting_period="Latest in-scope terms",
        data_source="``student_overview``",
        implementation=("app.analytics.queries:student_overview", "app.analytics.metrics:delta"),
    ),
    MetricDef(
        id="perf.class_rank",
        label="Class rank",
        kind=MetricKind.KPI,
        audience=ALL_ROLES,
        chart_keys=("student.kpis", "student.term_progress"),
        kpi_card_labels=("Class rank",),
        summary="Rank by latest term average among classmates; ties share rank.",
        numerator_denominator="rank in cohort by score",
        inclusion="Students in the same section with term averages",
        exclusion="Hidden from family when cohort benchmarks disabled",
        filter_semantics="Uses latest term with rank in ``student_term_progress``",
        reporting_period="Latest term in filter",
        data_source="Term progress query",
        implementation=(
            "app.analytics.queries:student_term_progress",
            "app.analytics.metrics:rank_and_percentile",
        ),
        privacy_note="Suppressed in family mode when cohort comparison is off.",
    ),
    MetricDef(
        id="perf.term_trajectory_slope",
        label="Direction of travel / term trend",
        kind=MetricKind.KPI,
        audience=ALL_ROLES,
        chart_keys=("student.kpis", "student.term_progress", "school.term_trend"),
        kpi_card_labels=("Direction of travel",),
        summary=(
            "Least-squares slope of term-average series in percentage points per term "
            "(student) or grade-by-term matrix (school trend chart)."
        ),
        numerator_denominator="OLS slope on term averages",
        inclusion="Terms with averages",
        exclusion="Single-term series → insufficient data",
        filter_semantics=(
            "``school.term_trend`` keeps all terms on the chart for comparison. "
            "A selected term is highlighted, not used to hide other terms. "
            "Grade, subject, section, and assessment-type filters still apply."
        ),
        reporting_period="Academic year (school trend); student terms in filter",
        data_source="Term aggregates",
        implementation=(
            "app.analytics.metrics:trend_slope",
            "app.analytics.metrics:term_trend_label",
            "app.analytics.charts:school_term_trend",
        ),
    ),
    MetricDef(
        id="perf.score_spread",
        label="Spread (consistency of results)",
        kind=MetricKind.KPI,
        audience=STAFF,
        chart_keys=("section.kpis",),
        kpi_card_labels=("Spread",),
        summary="Standard deviation of in-scope assessment percentages (lower = tighter).",
        numerator_denominator="stdev(score_values)",
        inclusion="All result percentages",
        exclusion="—",
        filter_semantics="Classroom scope",
        reporting_period="Filter window",
        data_source="``score_values``",
        implementation=(
            "app.analytics.metrics:consistency",
            "app.analytics.queries:cohort_overview",
        ),
        family_safe=False,
    ),
    MetricDef(
        id="attendance.student_rate",
        label="Attendance rate",
        kind=MetricKind.KPI,
        audience=ALL_ROLES,
        chart_keys=(
            "student.kpis",
            "section.kpis",
            "school.kpis",
            "student.attendance",
            "section.attendance",
            "school.attendance_scatter",
        ),
        kpi_card_labels=("Attendance",),
        summary=(
            "Official daily attendance: present days (per tenant policy for late/excused) "
            "divided by recorded days, then averaged across students for cohort KPIs."
        ),
        numerator_denominator="present_days / total_days per student; cohort uses mean",
        inclusion="Attendance rows in term/year/date filters",
        exclusion="Days outside filter; session-level attendance not used here",
        filter_semantics=(
            "``attendance_present_days`` respects ``attendance_count_late_as_present`` "
            "and ``attendance_count_excused_as_present`` tenant flags."
        ),
        reporting_period="Filtered term or academic year",
        data_source="``attendance`` (daily official)",
        implementation=(
            "app.analytics.queries:attendance_by_student",
            "app.analytics.metrics:attendance_percentage",
            "app.tenant.policy:attendance_present_days",
        ),
        privacy_note="Daily official attendance, not per-period session roll call.",
    ),
    MetricDef(
        id="attendance.monthly_mix",
        label="Attendance by month",
        kind=MetricKind.DERIVED,
        audience=ALL_ROLES,
        chart_keys=("student.attendance",),
        kpi_card_labels=(),
        summary="Stacked present/late/excused/absent day counts per calendar month.",
        numerator_denominator="counts by status per month",
        inclusion="Student's attendance rows",
        exclusion="—",
        filter_semantics="Student scoped",
        reporting_period="Months overlapping filter dates",
        data_source="``attendance``",
        implementation=("app.analytics.queries:attendance_monthly",),
    ),
    MetricDef(
        id="attendance.status_mix",
        label="Attendance status mix",
        kind=MetricKind.DERIVED,
        audience=ALL_ROLES,
        chart_keys=("student.attendance_status",),
        kpi_card_labels=(),
        summary=(
            "Mutually exclusive counts of present, late, excused, and absent "
            "official daily attendance days."
        ),
        numerator_denominator="count per status",
        inclusion="Attendance rows in filter window",
        exclusion="Months with zero recorded days omitted from chart",
        filter_semantics="Student and date/term filters",
        reporting_period="Filtered period",
        data_source="``attendance`` (daily official)",
        implementation=(
            "app.analytics.charts:student_attendance_status",
            "app.analytics.queries:attendance_by_student",
        ),
        privacy_note="Daily official attendance, not session roll call.",
    ),
    MetricDef(
        id="risk.composite_status",
        label="Student risk status",
        kind=MetricKind.CLASSIFICATION,
        audience=ALL_ROLES,
        chart_keys=(
            "student.kpis",
            "section.students_table",
            "section.at_risk",
            "school.at_risk",
        ),
        kpi_card_labels=("Status",),
        summary=(
            "Rule-based flag from average, failing subjects, term slide, attendance, "
            "recent drop, and concern remarks — descriptive, not diagnostic."
        ),
        numerator_denominator="weighted score → none | watch | high",
        inclusion="Students with enough signals in scope",
        exclusion="—",
        filter_semantics="Tenant at-risk and pass thresholds",
        reporting_period="Current filters; remarks lookback labeled on card",
        data_source="Grades, attendance, remarks",
        implementation=(
            "app.analytics.metrics:assess_risk",
            "app.analytics.queries:student_standings",
        ),
        privacy_note="Never presented as medical or disciplinary verdict.",
    ),
    MetricDef(
        id="risk.students_needing_support",
        label="Students needing support",
        kind=MetricKind.KPI,
        audience=ADMIN_ONLY | STAFF,
        chart_keys=("school.kpis", "section.kpis", "section.at_risk", "school.at_risk"),
        kpi_card_labels=("Needs attention", "Students needing support"),
        summary="Count of students with ``risk.is_at_risk`` in ``student_standings``.",
        numerator_denominator="count(at_risk)",
        inclusion="Watch or high risk levels",
        exclusion="On-track students",
        filter_semantics="Cohort filters",
        reporting_period="Filter window",
        data_source="``student_standings``",
        implementation=(
            "app.analytics.queries:cohort_overview",
            "app.analytics.metrics:assess_risk",
        ),
        family_safe=False,
    ),
    MetricDef(
        id="risk.high_risk_students",
        label="High-risk students",
        kind=MetricKind.DERIVED,
        audience=STAFF | ADMIN_ONLY,
        chart_keys=("school.kpis", "section.kpis", "school.at_risk"),
        kpi_card_labels=(),
        summary="Subset of at-risk cohort at ``high`` level.",
        numerator_denominator="count(level == high)",
        inclusion="High risk only",
        exclusion="Watch-level only",
        filter_semantics="Same as standings",
        reporting_period="Filter window",
        data_source="``student_standings``",
        implementation=("app.analytics.queries:cohort_overview",),
        family_safe=False,
    ),
    MetricDef(
        id="grade.letter_band",
        label="Letter grade",
        kind=MetricKind.CLASSIFICATION,
        audience=ALL_ROLES,
        chart_keys=(
            "student.kpis",
            "student.subject_table",
            "student.assessment_table",
            "section.grade_mix",
            "student.grade_mix",
        ),
        kpi_card_labels=(),
        summary="CBSE-style band from ``GRADE_BANDS`` on a percentage.",
        numerator_denominator="threshold lookup on pct",
        inclusion="Any numeric percentage",
        exclusion="None/absent → ``--``",
        filter_semantics="Static bands",
        reporting_period="N/A",
        data_source="Derived from scores",
        implementation=("app.analytics.metrics:letter_grade", "app.analytics.metrics:GRADE_BANDS"),
    ),
    MetricDef(
        id="grade.result_distribution",
        label="Score distribution",
        kind=MetricKind.DERIVED,
        audience=STAFF | ADMIN_ONLY,
        chart_keys=("school.distribution", "section.distribution"),
        kpi_card_labels=(),
        summary="Histogram bucket counts over ``DISTRIBUTION_BUCKETS``.",
        numerator_denominator="count per band",
        inclusion="Result percentages",
        exclusion="Absent",
        filter_semantics="Scope filters",
        reporting_period="Filter window",
        data_source="``score_values``",
        implementation=("app.analytics.metrics:distribution",),
        family_safe=False,
    ),
    MetricDef(
        id="grade.mix_doughnut",
        label="Grade mix",
        kind=MetricKind.DERIVED,
        audience=ALL_ROLES,
        chart_keys=("student.grade_mix", "section.grade_mix", "school.pass_rate"),
        kpi_card_labels=(),
        summary="Count of results (or subjects) per letter grade band.",
        numerator_denominator="count per letter grade",
        inclusion="Non-null percentages",
        exclusion="Zero-count bands omitted from doughnut",
        filter_semantics="Per chart data source",
        reporting_period="Filter window",
        data_source="Subject averages or raw results",
        implementation=("app.analytics.metrics:grade_counts",),
    ),
    MetricDef(
        id="perf.subject_strength_radar",
        label="Subject strength profile",
        kind=MetricKind.DERIVED,
        audience=ALL_ROLES,
        chart_keys=("student.subject_radar",),
        kpi_card_labels=(),
        summary="Radar of student vs class average per subject code.",
        numerator_denominator="paired subject averages",
        inclusion="Subjects assessed",
        exclusion="Family mode may strip class series",
        filter_semantics="``sanitize_family_chart``",
        reporting_period="Filter window",
        data_source="``student_subject_summaries``",
        implementation=("app.analytics.charts:student_subject_radar",),
    ),
    MetricDef(
        id="perf.matrix_cell_average",
        label="Heatmap cell average",
        kind=MetricKind.DERIVED,
        audience=STAFF | ADMIN_ONLY,
        chart_keys=("section.heatmap", "school.section_matrix"),
        kpi_card_labels=(),
        summary="Weighted average for each row/column pair in a matrix.",
        numerator_denominator="WEIGHTED_AVG_EXPR per group",
        inclusion="Cells with data",
        exclusion="Empty cells null",
        filter_semantics="Matrix dimensions (student/subject, section/subject, grade/term)",
        reporting_period="Filter window",
        data_source="``matrix_averages``",
        implementation=("app.analytics.queries:matrix_averages",),
        family_safe=False,
    ),
    MetricDef(
        id="teacher.effectiveness_summary",
        label="Results by teacher",
        kind=MetricKind.AGGREGATE,
        audience=ADMIN_ONLY,
        chart_keys=("school.teacher_effectiveness",),
        kpi_card_labels=(),
        summary="Weighted average and pass rate per teacher assignment groups.",
        numerator_denominator="SQL aggregates by teacher",
        inclusion="Scores linked via ``teacher_assignments``",
        exclusion="Unassigned assessments",
        filter_semantics="School scope",
        reporting_period="Filter window",
        data_source="``teacher_effectiveness`` query",
        implementation=("app.analytics.queries:teacher_effectiveness",),
        family_safe=False,
        privacy_note="Staff operational metric; not for student/public leaderboards.",
    ),
    MetricDef(
        id="staff.intervention_uncovered",
        label="Flagged pupils with no plan",
        kind=MetricKind.PLACEHOLDER,
        audience=STAFF,
        chart_keys=("staff.intervention_uncovered",),
        kpi_card_labels=(),
        summary="Intervention coverage table (feature-flagged; scaffolded empty rows).",
        numerator_denominator="TBD when intervention data wired",
        inclusion="—",
        exclusion="—",
        filter_semantics="``require_interventions_enabled``",
        reporting_period="—",
        data_source="Interventions domain (future)",
        implementation=("app.analytics.charts:staff_intervention_uncovered",),
        family_safe=False,
    ),
    MetricDef(
        id="staff.interventions_open",
        label="Open intervention cases",
        kind=MetricKind.PLACEHOLDER,
        audience=STAFF,
        chart_keys=("staff.interventions_open",),
        kpi_card_labels=(),
        summary="Open cases table scaffold.",
        numerator_denominator="TBD",
        inclusion="—",
        exclusion="—",
        filter_semantics="Feature flag",
        reporting_period="—",
        data_source="Interventions domain (future)",
        implementation=("app.analytics.charts:staff_interventions_open",),
        family_safe=False,
    ),
    MetricDef(
        id="staff.intervention_outcomes",
        label="Closed intervention outcomes",
        kind=MetricKind.PLACEHOLDER,
        audience=STAFF,
        chart_keys=("staff.intervention_outcomes",),
        kpi_card_labels=(),
        summary="Closed outcomes table scaffold.",
        numerator_denominator="TBD",
        inclusion="—",
        exclusion="—",
        filter_semantics="Feature flag",
        reporting_period="—",
        data_source="Interventions domain (future)",
        implementation=("app.analytics.charts:staff_intervention_outcomes",),
        family_safe=False,
    ),
    MetricDef(
        id="staff.intervention_status_mix",
        label="Intervention case status mix",
        kind=MetricKind.DERIVED,
        audience=STAFF,
        chart_keys=("staff.intervention_status",),
        kpi_card_labels=(),
        summary=(
            "Mutually exclusive counts of open, in progress, and resolved "
            "intervention cases."
        ),
        numerator_denominator="count per status",
        inclusion="Intervention cases in filter scope",
        exclusion="—",
        filter_semantics=(
            "``require_interventions_enabled``; class, grade, and student filters "
            "narrow the student cohort."
        ),
        reporting_period="All cases in scope (not limited by term)",
        data_source="``intervention_cases``",
        implementation=(
            "app.analytics.charts:staff_intervention_status",
            "app.analytics.queries:intervention_case_status_counts",
        ),
        family_safe=False,
    ),
    MetricDef(
        id="enrollment.class_students_count",
        label="Students (class)",
        kind=MetricKind.KPI,
        audience=STAFF,
        chart_keys=("section.kpis",),
        kpi_card_labels=("Students",),
        summary="Distinct students with results in the classroom scope.",
        numerator_denominator="count(distinct student_id)",
        inclusion="In-scope scores",
        exclusion="—",
        filter_semantics="Section pinned by classroom charts",
        reporting_period="Filter window",
        data_source="``cohort_overview``",
        implementation=("app.analytics.queries:cohort_overview",),
        family_safe=False,
    ),
    MetricDef(
        id="perf.attendance_vs_performance",
        label="Attendance versus performance",
        kind=MetricKind.DERIVED,
        audience=ADMIN_ONLY,
        chart_keys=("school.attendance_scatter",),
        kpi_card_labels=(),
        summary="Scatter of each student's attendance % vs overall average %.",
        numerator_denominator="paired (attendance, average)",
        inclusion="Students in standings",
        exclusion="Missing either axis omitted",
        filter_semantics="School scope",
        reporting_period="Filter window",
        data_source="``attendance_vs_performance``",
        implementation=("app.analytics.queries:attendance_vs_performance",),
        family_safe=False,
    ),
]

for _metric in _METRIC_LIST:
    METRICS[_metric.id] = _metric

@dataclass(frozen=True)
class ChartGuide:
    """Chart-level copy for F02 information panels (axes, filters, caveats)."""

    purpose: str
    x_axis: str
    y_axis: str
    aggregation: str
    filter_behavior: str
    caveats: str = ""
    limitations: str = ""


def _school_guides() -> dict[str, ChartGuide]:
    shared_filters = (
        "Academic year, term, grade, section, subject, and assessment type filters "
        "apply where noted. This chart's filter behavior may differ from others on "
        "the page—open About this chart for specifics."
    )
    return {
        "school.kpis": ChartGuide(
            purpose="Headline school performance and participation at a glance.",
            x_axis="—",
            y_axis="KPI values (mostly %)",
            aggregation="Weighted averages, counts, and rule-based labels per KPI.",
            filter_behavior=shared_filters,
            caveats="Small denominators can move rates quickly.",
        ),
        "school.term_trend": ChartGuide(
            purpose=(
                "Explore how average assessment performance changes across "
                "academic terms for each grade."
            ),
            x_axis="Academic terms in chronological order.",
            y_axis="Weighted average score (%) per grade line.",
            aggregation=(
                "Each point is the weighted mean of in-scope assessment results "
                "for that grade and term."
            ),
            filter_behavior=(
                "Grade, subject, section, and assessment-type filters apply. "
                "The term filter does not remove other terms: every term stays "
                "visible and the selected term is highlighted when chosen."
            ),
            caveats="Sparse terms can look noisy; compare sample sizes in exports.",
            limitations="Descriptive trends only—not a forecast.",
        ),
        "school.pass_rate": ChartGuide(
            purpose="Share of assessment results at or above the school pass mark.",
            x_axis="Result buckets",
            y_axis="Percentage of results",
            aggregation="Passed results / all in-scope results, as a percentage.",
            filter_behavior=shared_filters,
        ),
        "school.grade_averages": ChartGuide(
            purpose="Compare average performance across year groups.",
            x_axis="Grade",
            y_axis="Weighted average score (%)",
            aggregation="Weighted mean of results per grade.",
            filter_behavior=shared_filters,
        ),
        "school.subject_averages": ChartGuide(
            purpose="See which subjects lift or drag school-wide performance.",
            x_axis="Subject",
            y_axis="Weighted average score (%)",
            aggregation="Weighted mean of results per subject.",
            filter_behavior=shared_filters,
        ),
        "school.distribution": ChartGuide(
            purpose="How results spread across score bands.",
            x_axis="Score band (%)",
            y_axis="Number of results",
            aggregation="Count of results in each band.",
            filter_behavior=shared_filters,
        ),
        "school.assessment_type_averages": ChartGuide(
            purpose="Compare outcomes across assessment types (tests, projects, etc.).",
            x_axis="Assessment type",
            y_axis="Weighted average score (%)",
            aggregation="Weighted mean of results per assessment type.",
            filter_behavior=shared_filters,
        ),
        "school.section_matrix": ChartGuide(
            purpose="Spot weak class-and-subject pairings in one view.",
            x_axis="Subject",
            y_axis="Class (section)",
            aggregation="Cell = weighted average % for that class and subject.",
            filter_behavior=shared_filters,
            caveats="Empty cells mean no results for that pairing in the filter window.",
        ),
        "school.section_leaderboard": ChartGuide(
            purpose="Rank classes by overall average for the current filters.",
            x_axis="Class",
            y_axis="Average % and rank",
            aggregation="Weighted class average; optional row highlight for a picked class.",
            filter_behavior=(
                "A single class filter highlights that row but keeps all classes "
                "visible unless two or more explicit section picks narrow the list."
            ),
        ),
        "school.section_compare": ChartGuide(
            purpose="Compare class averages on one axis.",
            x_axis="Class",
            y_axis="Weighted average score (%)",
            aggregation="Weighted mean per section; optional benchmark line.",
            filter_behavior=(
                "A single class filter highlights that bar but keeps all classes "
                "visible unless multiple section picks narrow the chart."
            ),
        ),
        "school.at_risk": ChartGuide(
            purpose="Students flagged by school rules for follow-up (not a diagnosis).",
            x_axis="Student",
            y_axis="Status and reasons",
            aggregation="Rule version combines performance, attendance, and trend signals.",
            filter_behavior=shared_filters,
            limitations="Human review required; rules vary by school policy version.",
        ),
        "school.attendance_scatter": ChartGuide(
            purpose="Relate attendance % to overall academic average per student.",
            x_axis="Attendance rate (%)",
            y_axis="Overall average (%)",
            aggregation="One point per student with both values in scope.",
            filter_behavior=shared_filters,
            caveats="Correlation is not causation.",
        ),
        "school.teacher_effectiveness": ChartGuide(
            purpose="Outcome averages grouped by assigned teacher (operational view).",
            x_axis="Teacher",
            y_axis="Weighted average score (%)",
            aggregation="Weighted mean of results for assessments linked to each teacher.",
            filter_behavior=shared_filters,
            limitations="Does not measure teaching quality in isolation from cohort context.",
        ),
    }


def _section_guides() -> dict[str, ChartGuide]:
    shared_filters = (
        "Academic year, term, subject, and assessment type filters narrow results. "
        "Every classroom chart targets exactly one class—the teacher's default section "
        "or an explicit class pick in the filter bar."
    )
    return {
        "section.kpis": ChartGuide(
            purpose="Headline class performance, spread, attendance, and follow-up counts.",
            x_axis="—",
            y_axis="KPI values (mostly %)",
            aggregation="Weighted averages and counts for students in the selected class.",
            filter_behavior=shared_filters,
            caveats="At-risk counts follow the same rules as the school overview.",
        ),
        "section.distribution": ChartGuide(
            purpose="How many graded results fall in each score band for this class.",
            x_axis="Score band (%)",
            y_axis="Number of results",
            aggregation="Count of in-scope assessment results per band.",
            filter_behavior=shared_filters,
        ),
        "section.assessment_averages": ChartGuide(
            purpose="Compare papers within the class and spot high/low performers per test.",
            x_axis="Assessment",
            y_axis="Average score (%)",
            aggregation="Weighted mean per assessment; meta may list top and bottom scores.",
            filter_behavior=shared_filters,
        ),
        "section.subject_averages": ChartGuide(
            purpose="See which subjects are strongest or weakest for the class.",
            x_axis="Subject",
            y_axis="Weighted average score (%)",
            aggregation="Weighted mean of results per subject in the class.",
            filter_behavior=shared_filters,
        ),
        "section.heatmap": ChartGuide(
            purpose="Student-by-subject grid to spot individual weak spots quickly.",
            x_axis="Subject",
            y_axis="Student",
            aggregation="Cell = weighted average % for that student and subject.",
            filter_behavior=shared_filters,
            caveats="Blank cells mean no graded result for that pairing under current filters.",
        ),
        "section.grade_mix": ChartGuide(
            purpose="How many subjects (or results) sit in each grade bucket.",
            x_axis="Grade label",
            y_axis="Count",
            aggregation="Grade assignment from percentage thresholds; counts per label.",
            filter_behavior=shared_filters,
        ),
        "section.attendance": ChartGuide(
            purpose="Relate each student's attendance rate to their weighted average.",
            x_axis="Attendance rate (%)",
            y_axis="Weighted average score (%)",
            aggregation="One point per student in the class with both values in scope.",
            filter_behavior=(
                f"{shared_filters} Optional date filters limit the attendance window."
            ),
            caveats="Correlation is not causation.",
        ),
        "section.students_table": ChartGuide(
            purpose="Sortable roster with averages, attendance, and trend signals.",
            x_axis="Student row",
            y_axis="Columns (scores, attendance, flags)",
            aggregation="One row per student; values respect active filters.",
            filter_behavior=shared_filters,
        ),
        "section.at_risk": ChartGuide(
            purpose="Students flagged for follow-up in this class (rule-based, not a diagnosis).",
            x_axis="Student",
            y_axis="Status and reasons",
            aggregation="School at-risk rules applied to the filtered class cohort.",
            filter_behavior=shared_filters,
            limitations="Confirm with human judgment; policy version affects flags.",
        ),
        "section.toppers": ChartGuide(
            purpose="Highest-performing students in the class for the current filters.",
            x_axis="Rank",
            y_axis="Student and average %",
            aggregation="Ordered by weighted average with tie handling.",
            filter_behavior=shared_filters,
        ),
    }


def _student_guides() -> dict[str, ChartGuide]:
    shared_filters = (
        "Academic year, term, subject, and assessment type filters narrow which results "
        "count. Views always reflect one student—the signed-in learner, a parent's "
        "linked child, or a student selected in staff drill-down."
    )
    family_mode = (
        "On parent and student portals, class rank and cohort benchmark series may be "
        "hidden when the school uses self-comparison mode."
    )
    return {
        "student.kpis": ChartGuide(
            purpose="Snapshot of this learner's average, trend, attendance, and status.",
            x_axis="—",
            y_axis="KPI values (mostly %)",
            aggregation="Weighted means and rule-based labels for the selected student.",
            filter_behavior=shared_filters,
            caveats=family_mode,
        ),
        "student.subject_trend": ChartGuide(
            purpose=(
                "How scores move through the year—per assessment when one subject is "
                "picked, otherwise term averages by subject."
            ),
            x_axis="Assessment date or academic term (depends on subject filter).",
            y_axis="Score (%)",
            aggregation="Student line plus optional class average benchmark per point.",
            filter_behavior=(
                f"{shared_filters} Pick a subject to switch the axis to individual "
                "assessments."
            ),
            caveats=family_mode,
        ),
        "student.vs_class": ChartGuide(
            purpose="Subject-by-subject comparison to class average and class best.",
            x_axis="Subject",
            y_axis="Score (%)",
            aggregation="One bar group per subject: student, class mean, class top.",
            filter_behavior=shared_filters,
            caveats=family_mode,
            limitations="Hidden entirely on family portals in self-comparison mode.",
        ),
        "student.subject_radar": ChartGuide(
            purpose="Relative strength across subjects on one radial chart.",
            x_axis="Subject (spokes)",
            y_axis="Average score (%)",
            aggregation="Subject averages for the student vs optional class average ring.",
            filter_behavior=shared_filters,
            caveats=family_mode,
        ),
        "student.grade_mix": ChartGuide(
            purpose="How this student's subject averages distribute across grade labels.",
            x_axis="Grade label",
            y_axis="Number of subjects",
            aggregation="Grade bucket counts from subject-level averages.",
            filter_behavior=shared_filters,
        ),
        "student.attendance": ChartGuide(
            purpose="Month-by-month attendance mix for the learner.",
            x_axis="Calendar month",
            y_axis="Days by status",
            aggregation="Stacked counts (present, late, excused, absent) per month.",
            filter_behavior=(
                f"{shared_filters} Date filters can narrow which months appear."
            ),
        ),
        "student.attendance_status": ChartGuide(
            purpose="Share of official school days in each attendance status for this learner.",
            x_axis="Status category",
            y_axis="Day count",
            aggregation=(
                "Mutually exclusive counts of present, late, excused, and absent days "
                "from daily official attendance in the filtered period."
            ),
            filter_behavior=shared_filters,
            limitations="Daily official attendance only — not per-period session roll call.",
        ),
        "student.term_progress": ChartGuide(
            purpose="Weighted average each term against the class trajectory.",
            x_axis="Academic term",
            y_axis="Weighted average score (%)",
            aggregation="Per-term student average with optional class average bar.",
            filter_behavior=shared_filters,
            caveats=family_mode,
        ),
        "student.subject_table": ChartGuide(
            purpose="Detailed per-subject metrics including comparison and consistency.",
            x_axis="Subject row",
            y_axis="Columns (average, rank, trend, etc.)",
            aggregation="One row per subject under active filters.",
            filter_behavior=shared_filters,
            caveats=family_mode,
        ),
        "student.assessment_table": ChartGuide(
            purpose="Every graded assessment in scope, sortable by column.",
            x_axis="Assessment row",
            y_axis="Marks, percentage, class comparison",
            aggregation="One row per assessment result; respects date and type filters.",
            filter_behavior=shared_filters,
            caveats=family_mode,
        ),
        "student.remarks": ChartGuide(
            purpose="Chronological teacher remarks for the student.",
            x_axis="Date",
            y_axis="Remark text and context",
            aggregation="Entries from enabled remark feature, filtered by term when set.",
            filter_behavior=shared_filters,
            limitations="Requires remarks feature; content is staff-authored narrative.",
        ),
        "student.insights": ChartGuide(
            purpose="Staff-facing evidence summary with suggested follow-up actions.",
            x_axis="—",
            y_axis="Insight blocks",
            aggregation="Generated from performance, attendance, and trend signals.",
            filter_behavior=shared_filters,
            limitations="Administrators only; not shown on family portals.",
        ),
        "student.insight_summary": ChartGuide(
            purpose="Family-friendly progress snapshot without staff-only detail.",
            x_axis="—",
            y_axis="Summary sections",
            aggregation="Sanitized highlights from the same signals as full insights.",
            filter_behavior=shared_filters,
            limitations="Parent and student portals only; excludes class benchmarks in self mode.",
        ),
    }


def _staff_guides() -> dict[str, ChartGuide]:
    shared_filters = (
        "Grade, class, and student filters narrow which learners' cases are counted. "
        "Requires the interventions feature."
    )
    return {
        "staff.intervention_status": ChartGuide(
            purpose="Share of intervention cases in each workflow status.",
            x_axis="Status category",
            y_axis="Case count",
            aggregation=(
                "Counts of open, in progress, and resolved cases from "
                "``intervention_cases`` in the active scope."
            ),
            filter_behavior=shared_filters,
            limitations="Staff portals only; hidden when interventions are disabled.",
        ),
    }


_SCHOOL_CHART_GUIDES = _school_guides()
_SECTION_CHART_GUIDES = _section_guides()
_STUDENT_CHART_GUIDES = _student_guides()
_STAFF_CHART_GUIDES = _staff_guides()

CHART_GUIDES: dict[str, ChartGuide] = {
    **_SCHOOL_CHART_GUIDES,
    **_SECTION_CHART_GUIDES,
    **_STUDENT_CHART_GUIDES,
    **_STAFF_CHART_GUIDES,
}

PRINCIPAL_CHART_KEYS: frozenset[str] = frozenset(_SCHOOL_CHART_GUIDES)
SECTION_CHART_KEYS: frozenset[str] = frozenset(_SECTION_CHART_GUIDES)
STUDENT_CHART_KEYS: frozenset[str] = frozenset(_STUDENT_CHART_GUIDES)


def serialize_chart_guide(chart_key: str) -> dict[str, str] | None:
    guide = CHART_GUIDES.get(chart_key)
    if guide is None:
        return None
    payload: dict[str, str] = {
        "purpose": guide.purpose,
        "xAxis": guide.x_axis,
        "yAxis": guide.y_axis,
        "aggregation": guide.aggregation,
        "filterBehavior": guide.filter_behavior,
    }
    if guide.caveats:
        payload["caveats"] = guide.caveats
    if guide.limitations:
        payload["limitations"] = guide.limitations
    return payload


# Chart-only visuals without separate metric rows (inventory for F01-01 completeness).
CHART_ONLY_KEYS: tuple[str, ...] = (
    "student.subject_trend",
    "student.vs_class",
    "student.assessment_table",
    "student.remarks",
    "student.insights",
    "student.insight_summary",
    "section.assessment_averages",
    "section.students_table",
    "section.toppers",
    "school.assessment_type_averages",
)


@dataclass(frozen=True)
class ChartInventoryEntry:
    key: str
    title: str
    kind: str
    roles: tuple[str, ...]
    metric_ids: tuple[str, ...]


def _metrics_for_chart(chart_key: str) -> tuple[str, ...]:
    ids = [m.id for m in METRICS.values() if chart_key in m.chart_keys]
    return tuple(sorted(ids))


def chart_entries() -> tuple[ChartInventoryEntry, ...]:
    entries: list[ChartInventoryEntry] = []
    for definition in sorted(CHART_REGISTRY.values(), key=lambda d: d.key):
        entries.append(
            ChartInventoryEntry(
                key=definition.key,
                title=definition.title,
                kind=definition.kind,
                roles=tuple(_roles(definition.roles)),
                metric_ids=_metrics_for_chart(definition.key),
            )
        )
    return tuple(entries)


def get_metric(metric_id: str) -> MetricDef | None:
    return METRICS.get(metric_id)


def iter_metrics() -> Iterator[MetricDef]:
    yield from METRICS.values()


def metric_ids() -> frozenset[str]:
    return frozenset(METRICS)


def metrics_for_chart(chart_key: str) -> tuple[MetricDef, ...]:
    return tuple(m for m in METRICS.values() if chart_key in m.chart_keys)


def validate_registry() -> list[str]:
    """Return human-readable problems (empty list = OK). For F01-02 assertions."""
    problems: list[str] = []
    seen: set[str] = set()
    for metric in METRICS.values():
        if metric.id in seen:
            problems.append(f"duplicate metric id: {metric.id}")
        seen.add(metric.id)
        for chart_key in metric.chart_keys:
            if chart_key not in CHART_REGISTRY:
                problems.append(f"{metric.id}: unknown chart key {chart_key}")
    for chart_key in CHART_ONLY_KEYS:
        if chart_key not in CHART_REGISTRY:
            problems.append(f"CHART_ONLY_KEYS: unknown chart {chart_key}")
    return problems


def _metric_to_dict(metric: MetricDef) -> dict[str, Any]:
    payload = asdict(metric)
    payload["audience"] = _roles(metric.audience)
    payload["kind"] = metric.kind.value
    return payload


def export_registry_document() -> dict[str, Any]:
    """JSON-serializable registry for APIs and generated docs."""
    return {
        "version": REGISTRY_VERSION,
        "metric_count": len(METRICS),
        "chart_count": len(CHART_REGISTRY),
        "metrics": [_metric_to_dict(m) for m in sorted(METRICS.values(), key=lambda m: m.id)],
        "charts": [
            {
                "key": entry.key,
                "title": entry.title,
                "kind": entry.kind,
                "roles": list(entry.roles),
                "metric_ids": list(entry.metric_ids),
                "metrics_documented": bool(entry.metric_ids),
            }
            for entry in chart_entries()
        ],
        "chart_only_keys": list(CHART_ONLY_KEYS),
        "validation_errors": validate_registry(),
    }


def audit_entries() -> tuple[dict[str, Any], ...]:
    """Flat audit rows: one per metric with primary implementation pointer (F01-01 evidence)."""
    rows: list[dict[str, Any]] = []
    for metric in sorted(METRICS.values(), key=lambda m: m.id):
        rows.append(
            {
                "metric_id": metric.id,
                "label": metric.label,
                "primary_implementation": (
                    metric.implementation[0] if metric.implementation else None
                ),
                "chart_keys": list(metric.chart_keys),
                "audience": _roles(metric.audience),
                "version": metric.version,
            }
        )
    return tuple(rows)


def metric_visible(metric: MetricDef, role: Role) -> bool:
    if role not in metric.audience:
        return False
    if role in FAMILY and not metric.family_safe:
        return False
    return True


def serialize_metric_for_role(metric: MetricDef, role: Role) -> dict[str, Any]:
    """Glossary JSON shape for F01-03 (progressive disclosure by role)."""
    payload: dict[str, Any] = {
        "id": metric.id,
        "label": metric.label,
        "version": metric.version,
        "kind": metric.kind.value,
        "summary": metric.summary,
        "computation": metric.numerator_denominator,
        "includes": metric.inclusion,
        "excludes": metric.exclusion,
        "filterSemantics": metric.filter_semantics,
        "reportingPeriod": metric.reporting_period,
        "dataSource": metric.data_source,
        "chartKeys": list(metric.chart_keys),
        "kpiLabels": list(metric.kpi_card_labels),
    }
    if metric.known_limitations:
        payload["limitations"] = metric.known_limitations
    if metric.privacy_note:
        payload["privacyNote"] = metric.privacy_note
    if role in STAFF:
        payload["implementation"] = list(metric.implementation)
    return payload


def serialize_chart_info_for_role(chart_key: str, role: Role) -> dict[str, Any] | None:
    """Chart-level glossary payload for F01-04 / F02 info panels."""
    definition = CHART_REGISTRY.get(chart_key)
    if definition is None or role not in definition.roles:
        return None
    metrics = [
        serialize_metric_for_role(m, role)
        for m in metrics_for_chart(chart_key)
        if metric_visible(m, role)
    ]
    payload: dict[str, Any] = {
        "key": definition.key,
        "title": definition.title,
        "kind": definition.kind,
        "registryVersion": REGISTRY_VERSION,
        "metrics": metrics,
        "appendixHref": "/help/metrics",
    }
    chart_guide = serialize_chart_guide(chart_key)
    if chart_guide is not None:
        payload["chartGuide"] = chart_guide
    return payload


def search_metrics_for_role(
    role: Role,
    query: str | None = None,
) -> list[dict[str, Any]]:
    needle = (query or "").strip().lower()
    visible = [m for m in METRICS.values() if metric_visible(m, role)]
    if needle:
        visible = [
            m
            for m in visible
            if needle in m.id.lower()
            or needle in m.label.lower()
            or needle in m.summary.lower()
        ]
    visible.sort(key=lambda m: m.id)
    return [serialize_metric_for_role(m, role) for m in visible]


def write_generated_registry(root: Path | None = None) -> Path:
    """Write ``docs/generated/metrics_registry.yaml`` and ``.json`` from this module."""
    root = root or Path(__file__).resolve().parents[2]
    out_dir = root / "docs" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    document = export_registry_document()
    yaml_path = out_dir / "metrics_registry.yaml"
    json_path = out_dir / "metrics_registry.json"

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        yaml = None  # type: ignore[assignment]

    json_path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    if yaml is not None:
        yaml_path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    else:
        yaml_path.write_text(
            "# Install PyYAML to regenerate; see metrics_registry.json\n",
            encoding="utf-8",
        )
    return yaml_path


if __name__ == "__main__":
    path = write_generated_registry()
    errors = validate_registry()
    print(f"Wrote {path}; metrics={len(METRICS)} charts={len(CHART_REGISTRY)} errors={errors}")
