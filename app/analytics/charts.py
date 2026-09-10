"""Chart builders: turn query results into the payloads the front end renders.

Every visualization is registered in `CHART_REGISTRY` under a fixed key with the roles
allowed to request it. The HTTP layer looks a key up in that dictionary and nothing
else, so a client cannot name an arbitrary callable.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.analytics import metrics, queries
from app.deps import AccessScope
from app.models import Role, Student
from app.schemas import FilterParams
from app.tenant.features import require_remarks_enabled

ChartHandler = Callable[[Session, FilterParams, AccessScope], dict]

ALL_ROLES = frozenset({Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT})
STAFF = frozenset({Role.ADMIN, Role.TEACHER})
ADMIN_ONLY = frozenset({Role.ADMIN})


@dataclass(frozen=True)
class ChartDef:
    key: str
    title: str
    roles: frozenset[Role]
    handler: ChartHandler
    kind: str = "chart"


def _target_student(
    db: Session, filters: FilterParams, scope: AccessScope
) -> Student:
    """Resolve which student a student-level chart is about, within scope."""
    if scope.role in (Role.PARENT, Role.STUDENT):
        return scope.resolve_student(filters.student_id)
    if filters.student_id is None:
        # Staff have not named anyone yet, so open on the first student the
        # current filters allow rather than showing a row of error cards.
        student = queries.default_student(db, filters, scope)
        if student is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No student matches the current filters.",
            )
        return student
    scope.assert_student(filters.student_id)
    student = db.get(Student, filters.student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )
    scope.assert_tenant_record(student.tenant_id, "Student")
    return student


def _fmt(value: float | None, suffix: str = "%") -> str:
    return "--" if value is None else f"{value:.1f}{suffix}"


def _kpi(
    label: str,
    value: str,
    hint: str | None = None,
    tone: str = "neutral",
    delta: float | None = None,
) -> dict:
    return {"label": label, "value": value, "hint": hint, "tone": tone, "delta": delta}


def _matrix_to_series(matrix: dict) -> dict:
    """Reshape a row/column average matrix into labelled line or bar series."""
    return {
        "labels": [column["label"] for column in matrix["columns"]],
        "series": [
            {"label": row["label"], "data": matrix["values"][index]}
            for index, row in enumerate(matrix["rows"])
        ],
    }


# --------------------------------------------------------------------------- #
# Student level charts
# --------------------------------------------------------------------------- #


def student_kpis(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    student = _target_student(db, filters, scope)
    overview = queries.student_overview(db, student, filters, scope)
    risk = overview["risk"]

    cards = [
        _kpi(
            "Overall average",
            _fmt(overview["overall"]),
            f"Grade {overview['grade']} - {overview['descriptor']}",
            metrics.performance_tone(overview["overall"]),
        ),
        _kpi(
            "Versus class average",
            _fmt(overview["gap_to_class"], " pts")
            if overview["gap_to_class"] is not None
            else "--",
            f"Class average {_fmt(overview['class_overall'])}",
            "positive"
            if (overview["gap_to_class"] or 0) >= 0
            else "negative",
            overview["gap_to_class"],
        ),
        _kpi(
            "Class rank",
            f"{overview['rank']} of {overview['cohort']}"
            if overview["rank"]
            else "--",
            "Based on the latest term",
        ),
        _kpi(
            "Direction of travel",
            overview["trend"],
            f"{overview['term_slope']:+.1f} points per term"
            if overview["term_slope"] is not None
            else "Needs two terms of data",
            overview["trend_tone"],
        ),
        _kpi(
            "Attendance",
            _fmt(overview["attendance"]),
            f"{overview['attendance_detail'].get('present_days', 0)} of "
            f"{overview['attendance_detail'].get('total', 0)} days",
            "positive"
            if (overview["attendance"] or 0) >= 85
            else "warning",
        ),
        _kpi(
            "Status",
            risk.label,
            "; ".join(risk.reasons) if risk.reasons else "No concerns flagged",
            risk.tone,
        ),
    ]
    return {
        "kind": "kpi",
        "cards": cards,
        "meta": {
            "student": student.full_name,
            "section": f"{student.section.grade.name} - {student.section.name}",
            # Staff can land here without naming anyone, so say who is shown.
            "hint": f"{student.full_name} - {student.section.grade.name} "
            f"- {student.section.name}",
            "strengths": overview["strengths"],
            "weaknesses": overview["weaknesses"],
            "assessments": overview["assessment_count"],
            "consistency": metrics.consistency_label(overview["consistency"]),
        },
    }


def student_subject_trend(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    """Score progression across the year.

    With a subject selected the axis is every assessment for that subject, so a parent
    can see each test. With no subject selected it collapses to term averages per
    subject, which stays readable across eight subjects.
    """
    student = _target_student(db, filters, scope)
    student_filters = filters.with_student(student.id)

    subject_ids = filters.resolved_subject_ids() or []
    if len(subject_ids) > 1:
        matrix = queries.matrix_averages(
            db,
            student_filters,
            scope,
            row_dimension="term",
            column_dimension="subject",
        )
        labels = [row["label"] for row in matrix["rows"]]
        series = []
        for col in matrix["columns"]:
            if col["id"] in subject_ids:
                idx = matrix["columns"].index(col)
                series.append(
                    {
                        "label": col["label"],
                        "data": [matrix["values"][r][idx] for r in range(len(matrix["rows"]))],
                    }
                )
        return {
            "kind": "line",
            "labels": labels,
            "series": series,
            "meta": {"axis": "Term", "hint": "One line per selected subject."},
        }

    if filters.subject_id is not None or len(subject_ids) == 1:
        single_filters = student_filters
        if len(subject_ids) == 1:
            single_filters = student_filters.model_copy(
                update={"subject_id": subject_ids[0], "subject_ids": None}
            )
        payload = queries.student_assessment_series(db, student, single_filters, scope)
        facts = payload["assessments"]
        class_averages = payload["class_averages"]
        labels = [
            f"{fact.assessment_type.label} - {fact.conducted_on.strftime('%d %b')}"
            for fact in facts
        ]
        return {
            "kind": "line",
            "labels": labels,
            "series": [
                {
                    "label": student.full_name,
                    "data": [fact.pct for fact in facts],
                },
                {
                    "label": "Class average",
                    "data": [class_averages.get(fact.assessment_id) for fact in facts],
                    "kind": "dashed",
                },
            ],
            "meta": {
                "axis": "Assessment",
                "detail": [
                    {
                        "label": fact.assessment_name,
                        "date": fact.conducted_on.isoformat(),
                        "marks": fact.marks,
                        "max_marks": fact.max_marks,
                        "absent": fact.is_absent,
                    }
                    for fact in facts
                ],
            },
        }

    matrix = queries.matrix_averages(
        db, student_filters, scope, row_dimension="subject", column_dimension="term"
    )
    shaped = _matrix_to_series(matrix)
    return {
        "kind": "line",
        **shaped,
        "meta": {"axis": "Term", "hint": "Select a subject to see every assessment."},
    }


def student_vs_class(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    student = _target_student(db, filters, scope)
    summaries = queries.student_subject_summaries(db, student, filters, scope)
    return {
        "kind": "bar",
        "labels": [s.subject for s in summaries],
        "series": [
            {"label": student.full_name, "data": [s.student_avg for s in summaries]},
            {"label": "Class average", "data": [s.class_avg for s in summaries]},
            {"label": "Class best", "data": [s.class_best for s in summaries]},
        ],
        "meta": {"unit": "%"},
    }


def student_subject_radar(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    student = _target_student(db, filters, scope)
    summaries = queries.student_subject_summaries(db, student, filters, scope)
    return {
        "kind": "radar",
        "labels": [s.code for s in summaries],
        "series": [
            {"label": student.full_name, "data": [s.student_avg for s in summaries]},
            {"label": "Class average", "data": [s.class_avg for s in summaries]},
        ],
        "meta": {
            "subjects": [{"code": s.code, "name": s.subject} for s in summaries],
        },
    }


def student_grade_mix(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    student = _target_student(db, filters, scope)
    summaries = queries.student_subject_summaries(db, student, filters, scope)
    counts = metrics.grade_counts([s.student_avg for s in summaries])
    present = [(label, count) for label, count in counts if count > 0]
    return {
        "kind": "doughnut",
        "labels": [label for label, _ in present],
        "series": [{"label": "Subjects", "data": [count for _, count in present]}],
        "meta": {"total": sum(count for _, count in present)},
    }


def student_attendance(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    student = _target_student(db, filters, scope)
    rows = queries.attendance_monthly(db, student.id, filters)
    return {
        "kind": "stacked-bar",
        "labels": [row["label"] for row in rows],
        "series": [
            {"label": "Present", "data": [row["present"] for row in rows]},
            {"label": "Late", "data": [row["late"] for row in rows]},
            {"label": "Excused", "data": [row["excused"] for row in rows]},
            {"label": "Absent", "data": [row["absent"] for row in rows]},
        ],
        "meta": {
            "unit": "days",
            "percentages": [row["percentage"] for row in rows],
        },
    }


def student_term_progress(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    student = _target_student(db, filters, scope)
    rows = queries.student_term_progress(db, student, filters, scope)
    return {
        "kind": "bar",
        "labels": [row["term"] for row in rows],
        "series": [
            {"label": student.full_name, "data": [row["student_avg"] for row in rows]},
            {
                "label": "Class average",
                "data": [row["class_avg"] for row in rows],
                "kind": "line",
            },
        ],
        "meta": {
            "rows": [
                {
                    "term": row["term"],
                    "average": row["student_avg"],
                    "grade": row["grade"],
                    "rank": row["rank"],
                    "cohort": row["cohort"],
                    "percentile": row["percentile"],
                    "delta": row["delta"],
                }
                for row in rows
            ]
        },
    }


def student_subject_table(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    student = _target_student(db, filters, scope)
    summaries = queries.student_subject_summaries(db, student, filters, scope)
    return {
        "kind": "table",
        "columns": [
            {"key": "subject", "label": "Subject"},
            {"key": "average", "label": "Average", "align": "right"},
            {"key": "grade", "label": "Grade", "align": "center"},
            {"key": "class_avg", "label": "Class avg", "align": "right"},
            {"key": "gap", "label": "Gap", "align": "right"},
            {"key": "rank", "label": "Rank", "align": "center"},
            {"key": "percentile", "label": "Percentile", "align": "right"},
            {"key": "trend", "label": "Trend"},
            {"key": "consistency", "label": "Consistency"},
            {"key": "assessments", "label": "Assessed", "align": "right"},
        ],
        "rows": [
            {
                "subject": s.subject,
                "average": s.student_avg,
                "grade": s.grade,
                "class_avg": s.class_avg,
                "gap": s.gap_to_class,
                "rank": f"{s.rank} / {s.cohort}" if s.rank else "--",
                "percentile": s.percentile,
                "trend": s.trend,
                "consistency": metrics.consistency_label(s.consistency),
                "assessments": s.assessments,
                "_tone": metrics.performance_tone(s.student_avg),
            }
            for s in summaries
        ],
        "meta": {"student": student.full_name},
    }


def student_assessment_table(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    student = _target_student(db, filters, scope)
    payload = queries.student_assessment_series(
        db, student, filters.with_student(student.id), scope
    )
    facts = payload["assessments"]
    class_averages = payload["class_averages"]
    rows = []
    for fact in facts:
        class_avg = class_averages.get(fact.assessment_id)
        rows.append(
            {
                "date": fact.conducted_on.isoformat(),
                "subject": fact.subject_name,
                "assessment": fact.assessment_name,
                "type": fact.assessment_type.label,
                "term": fact.term_name,
                "marks": "Absent"
                if fact.is_absent
                else f"{fact.marks:g} / {fact.max_marks:g}",
                "percentage": fact.pct,
                "grade": metrics.letter_grade(fact.pct),
                "class_avg": class_avg,
                "gap": metrics.delta(fact.pct, class_avg),
                "_tone": "neutral" if fact.is_absent else metrics.performance_tone(fact.pct),
            }
        )
    rows.sort(key=lambda row: row["date"], reverse=True)
    return {
        "kind": "table",
        "columns": [
            {"key": "date", "label": "Date"},
            {"key": "subject", "label": "Subject"},
            {"key": "assessment", "label": "Assessment"},
            {"key": "type", "label": "Type"},
            {"key": "marks", "label": "Marks", "align": "right"},
            {"key": "percentage", "label": "%", "align": "right"},
            {"key": "grade", "label": "Grade", "align": "center"},
            {"key": "class_avg", "label": "Class avg", "align": "right"},
            {"key": "gap", "label": "Gap", "align": "right"},
        ],
        "rows": rows,
        "meta": {"student": student.full_name, "count": len(rows)},
    }


def student_remarks(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    require_remarks_enabled()
    student = _target_student(db, filters, scope)
    return {
        "kind": "timeline",
        "items": queries.student_remarks(
            db, student.id, filters, scope.tenant_id
        ),
        "meta": {"student": student.full_name},
    }


# --------------------------------------------------------------------------- #
# Classroom charts
# --------------------------------------------------------------------------- #


def _pick_classroom_section(section_ids: list[int], scope: AccessScope) -> int:
    """Pick one section when bookmarks/API pass multiple ``section_ids``.

    Classroom charts render a single class. When several ids are selected, a
    teacher sees their own assigned section if it is in the list; otherwise the
    first requested id the caller may access. Administrators take the first id.
    """
    if scope.section_ids is not None:
        allowed = set(section_ids)
        for assigned in sorted(scope.section_ids):
            if assigned in allowed:
                return assigned
    return section_ids[0]


def _section_filters(
    db: Session, filters: FilterParams, scope: AccessScope
) -> FilterParams:
    """Classroom charts always target exactly one section.

    Resolution order: explicit ``section_id``; a lone ``section_ids`` entry;
    when several ``section_ids`` are set, ``_pick_classroom_section``; else the
    tenant default (teacher home class or lowest grade/section for admins).
    """
    if filters.section_id is not None:
        scope.assert_section(filters.section_id)
        return filters.with_section(filters.section_id)

    section_ids = filters.resolved_section_ids()
    if section_ids is not None:
        if len(section_ids) == 1:
            section_id = section_ids[0]
        else:
            section_id = _pick_classroom_section(section_ids, scope)
        scope.assert_section(section_id)
        return filters.with_section(section_id)

    section_id = queries.default_section_id(db, filters, scope)
    if section_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No classroom matches the current filters.",
        )
    scope.assert_section(section_id)
    return filters.with_section(section_id)


def classroom_chart(handler: ChartHandler) -> ChartHandler:
    """Narrow a chart to a single classroom and name it in the payload.

    A teacher lands on their own class and a principal on the first class of the
    filtered grade, so the payload has to say which one was chosen.
    """

    @functools.wraps(handler)
    def wrapper(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
        scoped = _section_filters(db, filters, scope)
        payload = handler(db, scoped, scope)
        meta = payload.setdefault("meta", {})
        meta.setdefault("hint", queries.section_label(db, scoped.section_id))
        return payload

    return wrapper


@classroom_chart
def section_kpis(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    overview = queries.cohort_overview(db, filters, scope)
    values = queries.score_values(db, filters, scope)
    cards = [
        _kpi(
            "Class average",
            _fmt(overview["average"]),
            f"Median {_fmt(overview['median'])}",
            metrics.performance_tone(overview["average"]),
        ),
        _kpi("Pass rate", _fmt(overview["pass_rate"]), f"{overview['results']} results"),
        _kpi(
            "Students",
            str(overview["students"]),
            f"{overview['subjects']} subjects, {overview['assessments']} assessments",
        ),
        _kpi(
            "Spread",
            _fmt(overview["spread"], " pts"),
            metrics.consistency_label(overview["spread"]),
        ),
        _kpi(
            "Attendance",
            _fmt(overview["attendance"]),
            "Average across the class",
            "positive" if (overview["attendance"] or 0) >= 85 else "warning",
        ),
        _kpi(
            "Needs attention",
            str(overview["at_risk_count"]),
            f"{overview['high_risk_count']} at high risk",
            "negative" if overview["high_risk_count"] else "warning",
        ),
    ]
    return {
        "kind": "kpi",
        "cards": cards,
        "meta": {
            "highest": overview["highest"],
            "lowest": overview["lowest"],
            "distribution_total": len(values),
        },
    }


@classroom_chart
def section_distribution(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    values = queries.score_values(db, filters, scope)
    buckets = metrics.distribution(values)
    return {
        "kind": "bar",
        "labels": [label for label, _ in buckets],
        "series": [{"label": "Results", "data": [count for _, count in buckets]}],
        "meta": {"unit": "results", "total": len(values), "axis": "Score band (%)"},
    }


@classroom_chart
def section_assessment_averages(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    rows = queries.assessment_averages(db, filters, scope)
    return {
        "kind": "line",
        "labels": [row["short_label"] for row in rows],
        "series": [
            {"label": "Class average", "data": [row["average"] for row in rows]},
            {"label": "Highest", "data": [row["highest"] for row in rows], "kind": "dashed"},
            {"label": "Lowest", "data": [row["lowest"] for row in rows], "kind": "dashed"},
        ],
        "meta": {"detail": rows, "axis": "Assessment"},
    }


@classroom_chart
def section_subject_averages(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    rows = queries.grouped_averages(db, filters, scope, "subject")
    return {
        "kind": "bar",
        "labels": [row["label"] for row in rows],
        "series": [
            {"label": "Average", "data": [row["average"] for row in rows]},
            {"label": "Pass rate", "data": [row["pass_rate"] for row in rows]},
        ],
        "meta": {"detail": rows},
    }


@classroom_chart
def section_heatmap(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    matrix = queries.matrix_averages(db, filters, scope, "student", "subject")
    return {"kind": "heatmap", **matrix, "meta": {"unit": "%"}}


@classroom_chart
def section_grade_mix(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    values = queries.score_values(db, filters, scope)
    counts = [(label, count) for label, count in metrics.grade_counts(values) if count]
    return {
        "kind": "doughnut",
        "labels": [label for label, _ in counts],
        "series": [{"label": "Results", "data": [count for _, count in counts]}],
        "meta": {"total": len(values)},
    }


def _standings_table(
    standings: list[queries.StudentStanding], include_section: bool
) -> dict:
    columns = [
        {"key": "roll_no", "label": "Roll", "align": "right"},
        {"key": "name", "label": "Student"},
    ]
    if include_section:
        columns.append({"key": "section", "label": "Class"})
    columns += [
        {"key": "average", "label": "Average", "align": "right"},
        {"key": "grade", "label": "Grade", "align": "center"},
        {"key": "trend", "label": "Trend"},
        {"key": "attendance", "label": "Attendance", "align": "right"},
        {"key": "failing", "label": "Failing", "align": "right"},
        {"key": "status", "label": "Status"},
        {"key": "reasons", "label": "Why"},
    ]
    rows = []
    for standing in standings:
        row = {
            "roll_no": standing.roll_no,
            "name": standing.name,
            "average": standing.average,
            "grade": standing.grade_letter,
            "trend": standing.trend,
            "attendance": standing.attendance,
            "failing": standing.failing_subjects,
            "status": standing.risk.label,
            "reasons": "; ".join(standing.risk.reasons) or "--",
            "_tone": standing.risk.tone,
            "_student_id": standing.student_id,
        }
        if include_section:
            row["section"] = standing.section_label
        rows.append(row)
    return {"kind": "table", "columns": columns, "rows": rows}


@classroom_chart
def section_students_table(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    standings = queries.student_standings(db, filters, scope)
    payload = _standings_table(standings, include_section=False)
    payload["meta"] = {"count": len(standings)}
    return payload


@classroom_chart
def section_at_risk(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    standings = [
        s for s in queries.student_standings(db, filters, scope) if s.risk.is_at_risk
    ]
    standings.sort(key=lambda s: (s.average is None, s.average or 0))
    payload = _standings_table(standings, include_section=False)
    payload["meta"] = {"count": len(standings)}
    return payload


@classroom_chart
def section_toppers(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    standings = queries.student_standings(db, filters, scope, limit=10)
    payload = _standings_table(standings, include_section=False)
    payload["meta"] = {"count": len(standings)}
    return payload


@classroom_chart
def section_attendance(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    standings = queries.student_standings(db, filters, scope)
    ordered = sorted(standings, key=lambda s: s.roll_no)
    return {
        "kind": "bar",
        "labels": [s.name for s in ordered],
        "series": [
            {"label": "Attendance", "data": [s.attendance for s in ordered]},
            {"label": "Average score", "data": [s.average for s in ordered]},
        ],
        "meta": {"unit": "%"},
    }


# --------------------------------------------------------------------------- #
# School wide charts
# --------------------------------------------------------------------------- #


def school_kpis(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    overview = queries.cohort_overview(db, filters, scope)
    cards = [
        _kpi(
            "School average",
            _fmt(overview["average"]),
            f"Median {_fmt(overview['median'])}",
            metrics.performance_tone(overview["average"]),
        ),
        _kpi("Pass rate", _fmt(overview["pass_rate"]), f"{overview['results']} results"),
        _kpi(
            "Students assessed",
            str(overview["students"]),
            f"{overview['sections']} classes, {overview['subjects']} subjects",
        ),
        _kpi(
            "Assessments held",
            str(overview["assessments"]),
            "Across the selected period",
        ),
        _kpi(
            "Attendance",
            _fmt(overview["attendance"]),
            "School-wide average",
            "positive" if (overview["attendance"] or 0) >= 85 else "warning",
        ),
        _kpi(
            "Students needing support",
            str(overview["at_risk_count"]),
            f"{overview['high_risk_count']} at high risk",
            "negative" if overview["high_risk_count"] else "warning",
        ),
    ]
    return {"kind": "kpi", "cards": cards, "meta": {}}


def school_grade_averages(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    rows = queries.grouped_averages(db, filters, scope, "grade")
    return {
        "kind": "bar",
        "labels": [row["label"] for row in rows],
        "series": [
            {"label": "Average", "data": [row["average"] for row in rows]},
            {"label": "Pass rate", "data": [row["pass_rate"] for row in rows]},
        ],
        "meta": {"detail": rows},
    }


def school_subject_averages(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    rows = queries.grouped_averages(db, filters, scope, "subject")
    return {
        "kind": "bar",
        "labels": [row["label"] for row in rows],
        "series": [
            {"label": "Average", "data": [row["average"] for row in rows]},
            {"label": "Lowest", "data": [row["lowest"] for row in rows]},
            {"label": "Highest", "data": [row["highest"] for row in rows]},
        ],
        "meta": {"detail": rows},
    }


def school_assessment_type_averages(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    rows = queries.grouped_averages(db, filters, scope, "assessment_type")
    return {
        "kind": "bar",
        "labels": [row["label"] for row in rows],
        "series": [{"label": "Average", "data": [row["average"] for row in rows]}],
        "meta": {"detail": rows},
    }


def school_section_matrix(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    matrix = queries.matrix_averages(db, filters, scope, "section", "subject")
    return {"kind": "heatmap", **matrix, "meta": {"unit": "%"}}


def school_term_trend(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    # The trend is the point of this chart, so a single-term filter is ignored here.
    year_filters = filters.model_copy(update={"term_id": None})
    matrix = queries.matrix_averages(db, year_filters, scope, "grade", "term")
    shaped = _matrix_to_series(matrix)
    return {"kind": "line", **shaped, "meta": {"axis": "Term"}}


def school_pass_rate(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    values = queries.score_values(db, filters, scope)
    rate = metrics.pass_rate(values) or 0.0
    return {
        "kind": "gauge",
        "labels": ["Passed", "Below pass mark"],
        "series": [{"label": "Results", "data": [rate, round(100 - rate, 1)]}],
        "meta": {
            "value": rate,
            "threshold": metrics.settings.pass_percentage,
            "total": len(values),
        },
    }


def school_distribution(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    values = queries.score_values(db, filters, scope)
    buckets = metrics.distribution(values)
    return {
        "kind": "bar",
        "labels": [label for label, _ in buckets],
        "series": [{"label": "Results", "data": [count for _, count in buckets]}],
        "meta": {"unit": "results", "total": len(values), "axis": "Score band (%)"},
    }


def _section_comparison_context(
    filters: FilterParams,
) -> tuple[FilterParams, set[int]]:
    """Split section picks into query filters vs row highlights.

    The filter bar emits a single ``section_id``; that should focus a class on
    comparison charts without collapsing them to one row. Explicit ``section_ids``
    (repeated query params) narrows the comparison when two or more are given;
    a lone ``section_ids`` value falls back to all in-scope sections with a
    highlight on that class.
    """
    if filters.section_ids:
        if len(filters.section_ids) >= 2:
            return filters, set(filters.section_ids)
        section_id = filters.section_ids[0]
        return (
            filters.model_copy(update={"section_id": None, "section_ids": None}),
            {section_id},
        )
    if filters.section_id is not None:
        return (
            filters.model_copy(update={"section_id": None}),
            {filters.section_id},
        )
    return filters, set()


def school_section_leaderboard(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    query_filters, highlighted = _section_comparison_context(filters)
    rows = queries.grouped_averages(db, query_filters, scope, "section")
    return {
        "kind": "table",
        "columns": [
            {"key": "rank", "label": "#", "align": "right"},
            {"key": "label", "label": "Class"},
            {"key": "average", "label": "Average", "align": "right"},
            {"key": "pass_rate", "label": "Pass rate", "align": "right"},
            {"key": "highest", "label": "Best result", "align": "right"},
            {"key": "lowest", "label": "Lowest result", "align": "right"},
            {"key": "students", "label": "Students", "align": "right"},
        ],
        "rows": [
            {
                "rank": index,
                "label": row["label"],
                "average": row["average"],
                "pass_rate": row["pass_rate"],
                "highest": row["highest"],
                "lowest": row["lowest"],
                "students": row["students"],
                "_tone": metrics.performance_tone(row["average"]),
                "_highlight": row["id"] in highlighted if highlighted else False,
            }
            for index, row in enumerate(rows, start=1)
        ],
        "meta": {"count": len(rows), "highlighted": sorted(highlighted)},
    }


def school_section_compare(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    """Grouped bar comparing class averages (optionally filtered by subject)."""
    query_filters, highlighted = _section_comparison_context(filters)
    rows = queries.grouped_averages(db, query_filters, scope, "section")
    meta: dict[str, object] = {"detail": rows, "unit": "%", "highlighted": sorted(highlighted)}
    if len(rows) < 2:
        meta["hint"] = (
            "Only one class matches these filters. Clear the class filter or "
            "pick a broader grade to compare classes."
        )
    return {
        "kind": "bar",
        "labels": [row["label"] for row in rows],
        "series": [
            {"label": "Class average", "data": [row["average"] for row in rows]},
            {"label": "Pass rate", "data": [row["pass_rate"] for row in rows]},
        ],
        "meta": meta,
    }


def student_insights(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    student = _target_student(db, filters, scope)
    data = queries.build_student_insights(
        db, student, filters.with_student(student.id), scope
    )
    return {"kind": "insights", **data}


PARENT_STUDENT = frozenset({Role.PARENT, Role.STUDENT})


def student_insight_summary(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    student = _target_student(db, filters, scope)
    data = queries.build_parent_insight_summary(
        db, student, filters.with_student(student.id), scope
    )
    return {"kind": "insight_summary", **data}


def school_at_risk(db: Session, filters: FilterParams, scope: AccessScope) -> dict:
    standings = [s for s in queries.student_standings(db, filters, scope) if s.risk.is_at_risk]
    standings.sort(key=lambda s: (s.risk.level != "high", s.average is None, s.average or 0))
    payload = _standings_table(standings, include_section=True)
    payload["meta"] = {
        "count": len(standings),
        "high": sum(1 for s in standings if s.risk.level == "high"),
    }
    return payload


def school_attendance_scatter(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    points = queries.attendance_vs_performance(db, filters, scope)
    return {
        "kind": "scatter",
        "series": [{"label": "Students", "data": points}],
        "meta": {
            "x_label": "Attendance (%)",
            "y_label": "Average score (%)",
            "count": len(points),
        },
    }


def school_teacher_effectiveness(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict:
    """Average result per teacher across the classes and subjects they own."""
    rows = queries.teacher_effectiveness(db, filters, scope)
    return {
        "kind": "table",
        "columns": [
            {"key": "teacher", "label": "Teacher"},
            {"key": "department", "label": "Department"},
            {"key": "sections", "label": "Classes", "align": "right"},
            {"key": "subjects", "label": "Subjects", "align": "right"},
            {"key": "students", "label": "Students", "align": "right"},
            {"key": "average", "label": "Average", "align": "right"},
            {"key": "pass_rate", "label": "Pass rate", "align": "right"},
            {"key": "results", "label": "Results", "align": "right"},
        ],
        "rows": [
            {**row, "_tone": metrics.performance_tone(row["average"])} for row in rows
        ],
        "meta": {"count": len(rows)},
    }


CHART_DEFS: tuple[ChartDef, ...] = (
    # Student level
    ChartDef("student.kpis", "Performance summary", ALL_ROLES, student_kpis, "kpi"),
    ChartDef("student.subject_trend", "Scores through the year", ALL_ROLES, student_subject_trend),
    ChartDef("student.vs_class", "You versus the class", ALL_ROLES, student_vs_class),
    ChartDef("student.subject_radar", "Subject strength profile", ALL_ROLES, student_subject_radar),
    ChartDef("student.grade_mix", "Grade spread across subjects", ALL_ROLES, student_grade_mix),
    ChartDef("student.attendance", "Attendance by month", ALL_ROLES, student_attendance),
    ChartDef("student.term_progress", "Term on term progress", ALL_ROLES, student_term_progress),
    ChartDef(
        "student.subject_table", "Subject breakdown", ALL_ROLES,
        student_subject_table, "table",
    ),
    ChartDef(
        "student.assessment_table", "Every assessment", ALL_ROLES,
        student_assessment_table, "table",
    ),
    ChartDef("student.remarks", "Teacher remarks", ALL_ROLES, student_remarks, "timeline"),
    ChartDef(
        "student.insights", "Performance insights", ADMIN_ONLY,
        student_insights, "insights",
    ),
    ChartDef(
        "student.insight_summary", "Progress summary", PARENT_STUDENT,
        student_insight_summary, "insight_summary",
    ),
    # Classroom level
    ChartDef("section.kpis", "Classroom summary", STAFF, section_kpis, "kpi"),
    ChartDef("section.distribution", "Score distribution", STAFF, section_distribution),
    ChartDef(
        "section.assessment_averages", "Average by assessment", STAFF,
        section_assessment_averages,
    ),
    ChartDef("section.subject_averages", "Average by subject", STAFF, section_subject_averages),
    ChartDef("section.heatmap", "Student by subject heatmap", STAFF, section_heatmap, "heatmap"),
    ChartDef("section.grade_mix", "Grade distribution", STAFF, section_grade_mix),
    ChartDef("section.attendance", "Attendance against performance", STAFF, section_attendance),
    ChartDef("section.students_table", "All students", STAFF, section_students_table, "table"),
    ChartDef("section.at_risk", "Students needing attention", STAFF, section_at_risk, "table"),
    ChartDef("section.toppers", "Top performers", STAFF, section_toppers, "table"),
    # School level
    ChartDef("school.kpis", "School summary", ADMIN_ONLY, school_kpis, "kpi"),
    ChartDef("school.grade_averages", "Performance by grade", ADMIN_ONLY, school_grade_averages),
    ChartDef(
        "school.subject_averages", "Performance by subject", ADMIN_ONLY,
        school_subject_averages,
    ),
    ChartDef(
        "school.assessment_type_averages", "Performance by assessment type",
        ADMIN_ONLY, school_assessment_type_averages,
    ),
    ChartDef(
        "school.section_matrix", "Class by subject heatmap", ADMIN_ONLY,
        school_section_matrix, "heatmap",
    ),
    ChartDef("school.term_trend", "Term trend by grade", ADMIN_ONLY, school_term_trend),
    ChartDef("school.pass_rate", "Pass rate", ADMIN_ONLY, school_pass_rate),
    ChartDef("school.distribution", "School score distribution", ADMIN_ONLY, school_distribution),
    ChartDef(
        "school.section_leaderboard", "Class leaderboard", ADMIN_ONLY,
        school_section_leaderboard, "table",
    ),
    ChartDef(
        "school.section_compare", "Class comparison", ADMIN_ONLY,
        school_section_compare,
    ),
    ChartDef(
        "school.at_risk", "School-wide at-risk cohort", ADMIN_ONLY,
        school_at_risk, "table",
    ),
    ChartDef(
        "school.attendance_scatter", "Attendance versus performance", ADMIN_ONLY,
        school_attendance_scatter,
    ),
    ChartDef(
        "school.teacher_effectiveness", "Results by teacher", ADMIN_ONLY,
        school_teacher_effectiveness, "table",
    ),
)

CHART_REGISTRY: dict[str, ChartDef] = {
    definition.key: definition for definition in CHART_DEFS
}
