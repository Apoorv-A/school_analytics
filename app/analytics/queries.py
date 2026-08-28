"""Scope-aware database queries behind every dashboard.

Two access patterns are used deliberately:

* Narrow scopes (one student, one classroom) fetch `ScoreFact` rows and aggregate in
  Python, because those views need per-assessment detail anyway.
* Wide scopes (a grade, the whole school) aggregate in SQL with `GROUP BY`, so the
  principal's dashboard stays fast as the school grows.

Every query starts from `_conditions`, which applies the caller's `AccessScope`
alongside the requested filters. A handler cannot read outside its scope because the
scope predicates are added here, not by the caller.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.analytics import metrics
from app.deps import AccessScope
from app.models import (
    AcademicYear,
    Assessment,
    AssessmentType,
    Attendance,
    AttendanceStatus,
    Grade,
    Remark,
    Score,
    Section,
    Student,
    Subject,
    Teacher,
    TeacherAssignment,
    Term,
    User,
)
from app.schemas import FilterOption, FilterOptions, FilterParams

MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# marks / max_marks as a percentage, computed in SQL.
PCT_EXPR: ColumnElement[float] = Score.marks_obtained / Assessment.max_marks * 100.0

# Weighted average percentage: sum(pct * weight) / sum(weight).
_WEIGHTED_NUM = func.sum(PCT_EXPR * Assessment.weightage)
_WEIGHTED_DEN = func.sum(Assessment.weightage)
WEIGHTED_AVG_EXPR: ColumnElement[float] = case(
    (_WEIGHTED_DEN > 0, _WEIGHTED_NUM / _WEIGHTED_DEN), else_=func.avg(PCT_EXPR)
)


@dataclass(frozen=True)
class ScoreFact:
    """One graded result, denormalised for in-memory analysis."""

    student_id: int
    student_name: str
    roll_no: int
    section_id: int
    section_label: str
    grade_id: int
    grade_name: str
    subject_id: int
    subject_name: str
    subject_code: str
    term_id: int
    term_name: str
    term_sequence: int
    assessment_id: int
    assessment_name: str
    assessment_type: AssessmentType
    conducted_on: date
    max_marks: float
    weightage: float
    marks: float | None
    is_absent: bool

    @property
    def pct(self) -> float | None:
        return metrics.percentage(self.marks, self.max_marks)


@dataclass(frozen=True)
class SubjectSummary:
    subject_id: int
    subject: str
    code: str
    student_avg: float | None
    class_avg: float | None
    class_best: float | None
    rank: int | None
    cohort: int | None
    percentile: float | None
    slope: float | None
    consistency: float | None
    assessments: int

    @property
    def grade(self) -> str:
        return metrics.letter_grade(self.student_avg)

    @property
    def gap_to_class(self) -> float | None:
        return metrics.delta(self.student_avg, self.class_avg)

    @property
    def trend(self) -> str:
        return metrics.trend_label(self.slope)


@dataclass(frozen=True)
class StudentStanding:
    """A student's overall position, used by classroom and school tables."""

    student_id: int
    name: str
    roll_no: int
    section_id: int
    section_label: str
    grade_name: str
    average: float | None
    term_slope: float | None
    attendance: float | None
    failing_subjects: int
    subjects_assessed: int

    @property
    def risk(self) -> metrics.RiskAssessment:
        return metrics.assess_risk(
            self.average, self.term_slope, self.attendance, self.failing_subjects
        )

    @property
    def trend(self) -> str:
        return metrics.term_trend_label(self.term_slope)

    @property
    def trend_tone(self) -> str:
        return metrics.term_trend_tone(self.term_slope)

    @property
    def grade_letter(self) -> str:
        return metrics.letter_grade(self.average)


# --------------------------------------------------------------------------- #
# Filter and scope predicates
# --------------------------------------------------------------------------- #


def _scope_conditions(scope: AccessScope) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if scope.student_ids is not None:
        if not scope.student_ids:
            # An empty allow-list must match nothing rather than everything.
            return [func.coalesce(Score.student_id, -1) == -1]
        conditions.append(Score.student_id.in_(scope.student_ids))
    if scope.section_subjects is not None:
        if not scope.section_subjects:
            return [func.coalesce(Assessment.section_id, -1) == -1]
        conditions.append(
            or_(
                *[
                    and_(
                        Assessment.section_id == section_id,
                        Assessment.subject_id.in_(subject_ids),
                    )
                    for section_id, subject_ids in scope.section_subjects.items()
                ]
            )
        )
    elif scope.section_ids is not None and scope.student_ids is None:
        if not scope.section_ids:
            return [func.coalesce(Assessment.section_id, -1) == -1]
        conditions.append(Assessment.section_id.in_(scope.section_ids))
    return conditions


def _filter_conditions(filters: FilterParams) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.academic_year_id is not None:
        conditions.append(Term.academic_year_id == filters.academic_year_id)
    if filters.term_id is not None:
        conditions.append(Assessment.term_id == filters.term_id)
    if filters.subject_id is not None:
        conditions.append(Assessment.subject_id == filters.subject_id)
    if filters.grade_id is not None:
        conditions.append(Section.grade_id == filters.grade_id)
    if filters.section_id is not None:
        conditions.append(Assessment.section_id == filters.section_id)
    if filters.student_id is not None:
        conditions.append(Score.student_id == filters.student_id)
    if filters.assessment_type is not None:
        conditions.append(Assessment.assessment_type == filters.assessment_type)
    if filters.date_from is not None:
        conditions.append(Assessment.conducted_on >= filters.date_from)
    if filters.date_to is not None:
        conditions.append(Assessment.conducted_on <= filters.date_to)
    if filters.teacher_id is not None:
        conditions.append(
            select(TeacherAssignment.id)
            .where(
                TeacherAssignment.teacher_id == filters.teacher_id,
                TeacherAssignment.section_id == Assessment.section_id,
                TeacherAssignment.subject_id == Assessment.subject_id,
            )
            .exists()
        )
    return conditions


def _base_select(
    *columns,
    filters: FilterParams,
    scope: AccessScope,
    graded_only: bool = True,
) -> Select:
    """Join the score fact table and apply both scope and filter predicates."""
    stmt = (
        select(*columns)
        # Score is always the left side; without this the FROM is ambiguous whenever
        # the first selected column belongs to a joined table.
        .select_from(Score)
        .join(Assessment, Score.assessment_id == Assessment.id)
        .join(Section, Assessment.section_id == Section.id)
        .join(Grade, Section.grade_id == Grade.id)
        .join(Subject, Assessment.subject_id == Subject.id)
        .join(Term, Assessment.term_id == Term.id)
        .join(Student, Score.student_id == Student.id)
    )
    if graded_only:
        stmt = stmt.where(
            Score.is_absent.is_(False), Score.marks_obtained.is_not(None)
        )
    for condition in (*_scope_conditions(scope), *_filter_conditions(filters)):
        stmt = stmt.where(condition)
    return stmt


def fetch_facts(
    db: Session,
    filters: FilterParams,
    scope: AccessScope,
    graded_only: bool = False,
) -> list[ScoreFact]:
    """Load individual results. Use only for a student or a classroom sized scope."""
    stmt = _base_select(
        Score.student_id,
        Student.full_name,
        Student.roll_no,
        Section.id.label("section_id"),
        Grade.name.label("grade_name"),
        Section.name.label("section_name"),
        Grade.id.label("grade_id"),
        Subject.id.label("subject_id"),
        Subject.name.label("subject_name"),
        Subject.code.label("subject_code"),
        Term.id.label("term_id"),
        Term.name.label("term_name"),
        Term.sequence.label("term_sequence"),
        Assessment.id.label("assessment_id"),
        Assessment.name.label("assessment_name"),
        Assessment.assessment_type,
        Assessment.conducted_on,
        Assessment.max_marks,
        Assessment.weightage,
        Score.marks_obtained,
        Score.is_absent,
        filters=filters,
        scope=scope,
        graded_only=graded_only,
    ).order_by(Assessment.conducted_on, Assessment.id, Student.roll_no)

    facts: list[ScoreFact] = []
    for row in db.execute(stmt).all():
        facts.append(
            ScoreFact(
                student_id=row.student_id,
                student_name=row.full_name,
                roll_no=row.roll_no,
                section_id=row.section_id,
                section_label=f"{row.grade_name} - {row.section_name}",
                grade_id=row.grade_id,
                grade_name=row.grade_name,
                subject_id=row.subject_id,
                subject_name=row.subject_name,
                subject_code=row.subject_code,
                term_id=row.term_id,
                term_name=row.term_name,
                term_sequence=row.term_sequence,
                assessment_id=row.assessment_id,
                assessment_name=row.assessment_name,
                assessment_type=row.assessment_type,
                conducted_on=row.conducted_on,
                max_marks=row.max_marks,
                weightage=row.weightage,
                marks=row.marks_obtained,
                is_absent=row.is_absent,
            )
        )
    return facts


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #


def attendance_by_student(
    db: Session,
    student_ids: list[int] | None,
    filters: FilterParams,
    scope: AccessScope,
) -> dict[int, dict[str, float | int | None]]:
    """Attendance totals per student, honouring the term and date filters."""
    stmt = (
        select(
            Attendance.student_id,
            Attendance.status,
            func.count(Attendance.id).label("days"),
        )
        .join(Student, Attendance.student_id == Student.id)
        .join(Section, Student.section_id == Section.id)
        .group_by(Attendance.student_id, Attendance.status)
    )

    if student_ids is not None:
        if not student_ids:
            return {}
        stmt = stmt.where(Attendance.student_id.in_(student_ids))
    elif scope.student_ids is not None:
        if not scope.student_ids:
            return {}
        stmt = stmt.where(Attendance.student_id.in_(scope.student_ids))
    elif scope.section_ids is not None:
        stmt = stmt.where(Student.section_id.in_(scope.section_ids or {-1}))

    if filters.term_id is not None:
        stmt = stmt.where(Attendance.term_id == filters.term_id)
    if filters.academic_year_id is not None:
        stmt = stmt.where(
            Attendance.term_id.in_(
                select(Term.id).where(Term.academic_year_id == filters.academic_year_id)
            )
        )
    if filters.grade_id is not None:
        stmt = stmt.where(Section.grade_id == filters.grade_id)
    if filters.section_id is not None:
        stmt = stmt.where(Student.section_id == filters.section_id)
    if filters.date_from is not None:
        stmt = stmt.where(Attendance.on_date >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Attendance.on_date <= filters.date_to)

    tally: dict[int, dict[str, int]] = defaultdict(
        lambda: {status.value: 0 for status in AttendanceStatus}
    )
    for student_id, status, days in db.execute(stmt).all():
        key = status.value if isinstance(status, AttendanceStatus) else str(status)
        tally[student_id][key] = days

    result: dict[int, dict[str, float | int | None]] = {}
    for student_id, counts in tally.items():
        total = sum(counts.values())
        present = (
            counts[AttendanceStatus.PRESENT.value] + counts[AttendanceStatus.LATE.value]
        )
        result[student_id] = {
            **counts,
            "total": total,
            "present_days": present,
            "percentage": metrics.attendance_percentage(present, total),
        }
    return result


def attendance_monthly(
    db: Session, student_id: int, filters: FilterParams
) -> list[dict[str, object]]:
    """Month-by-month attendance breakdown for one student."""
    month_expr = func.strftime("%Y-%m", Attendance.on_date)
    stmt = (
        select(month_expr.label("month"), Attendance.status, func.count(Attendance.id))
        .where(Attendance.student_id == student_id)
        .group_by("month", Attendance.status)
        .order_by("month")
    )
    if filters.term_id is not None:
        stmt = stmt.where(Attendance.term_id == filters.term_id)
    if filters.academic_year_id is not None:
        stmt = stmt.where(
            Attendance.term_id.in_(
                select(Term.id).where(Term.academic_year_id == filters.academic_year_id)
            )
        )
    if filters.date_from is not None:
        stmt = stmt.where(Attendance.on_date >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Attendance.on_date <= filters.date_to)

    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {status.value: 0 for status in AttendanceStatus}
    )
    for month, status, count in db.execute(stmt).all():
        key = status.value if isinstance(status, AttendanceStatus) else str(status)
        buckets[month][key] = count

    rows: list[dict[str, object]] = []
    for month in sorted(buckets):
        year_part, month_part = month.split("-")
        counts = buckets[month]
        total = sum(counts.values())
        present = (
            counts[AttendanceStatus.PRESENT.value] + counts[AttendanceStatus.LATE.value]
        )
        rows.append(
            {
                "label": f"{MONTH_LABELS[int(month_part) - 1]} {year_part[2:]}",
                **counts,
                "total": total,
                "percentage": metrics.attendance_percentage(present, total),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Student level analytics
# --------------------------------------------------------------------------- #


def _cohort_averages_by_dimension(
    db: Session,
    filters: FilterParams,
    scope: AccessScope,
    section_id: int,
    dimension: ColumnElement,
) -> dict[int, list[float]]:
    """Per-student weighted averages inside one classroom, keyed by `dimension`.

    Returns values only, never names, so it is safe to run under an `aggregate_only`
    cohort view. This is what turns a parent's "my child versus the class" comparison
    into real numbers without disclosing who the classmates are.
    """
    cohort_scope = scope.cohort_view()
    cohort_filters = filters.model_copy(
        update={"student_id": None, "section_id": section_id}
    )
    stmt = _base_select(
        dimension.label("dim"),
        Score.student_id,
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        filters=cohort_filters,
        scope=cohort_scope,
    ).group_by(dimension, Score.student_id)

    buckets: dict[int, list[float]] = defaultdict(list)
    for dim_value, _student_id, avg in db.execute(stmt).all():
        if avg is not None:
            buckets[dim_value].append(round(avg, 2))
    return buckets


def _student_series_by_subject(
    db: Session, student_id: int, filters: FilterParams, scope: AccessScope
) -> tuple[dict[int, list[float | None]], dict[int, tuple[str, str]]]:
    """One student's chronological percentages per subject, plus subject labels."""
    stmt = _base_select(
        Assessment.subject_id,
        Subject.name,
        Subject.code,
        PCT_EXPR.label("pct"),
        filters=filters.model_copy(update={"student_id": student_id}),
        scope=scope,
    ).order_by(Assessment.conducted_on, Assessment.id)

    series: dict[int, list[float | None]] = defaultdict(list)
    meta: dict[int, tuple[str, str]] = {}
    for subject_id, name, code, pct in db.execute(stmt).all():
        meta[subject_id] = (name, code)
        series[subject_id].append(round(pct, 2) if pct is not None else None)
    return series, meta


def _student_weighted_by_subject(
    db: Session, student_id: int, filters: FilterParams, scope: AccessScope
) -> dict[int, float]:
    stmt = _base_select(
        Assessment.subject_id,
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        filters=filters.model_copy(update={"student_id": student_id}),
        scope=scope,
    ).group_by(Assessment.subject_id)
    return {
        subject_id: round(avg, 2)
        for subject_id, avg in db.execute(stmt).all()
        if avg is not None
    }


def student_subject_summaries(
    db: Session, student: Student, filters: FilterParams, scope: AccessScope
) -> list[SubjectSummary]:
    """Per-subject standing for one student, benchmarked against their own section."""
    scope.assert_student(student.id)

    series, meta = _student_series_by_subject(db, student.id, filters, scope)
    own_averages = _student_weighted_by_subject(db, student.id, filters, scope)
    cohort = _cohort_averages_by_dimension(
        db, filters, scope, student.section_id, Assessment.subject_id
    )

    summaries: list[SubjectSummary] = []
    for subject_id, (name, code) in sorted(meta.items(), key=lambda item: item[1][0]):
        student_avg = own_averages.get(subject_id)
        peers = cohort.get(subject_id, [])
        rank, cohort_size, percentile = metrics.rank_and_percentile(student_avg, peers)
        subject_series = series.get(subject_id, [])
        summaries.append(
            SubjectSummary(
                subject_id=subject_id,
                subject=name,
                code=code,
                student_avg=student_avg,
                class_avg=metrics.safe_mean(peers),
                class_best=max(peers) if peers else None,
                rank=rank,
                cohort=cohort_size,
                percentile=percentile,
                slope=metrics.trend_slope(subject_series),
                consistency=metrics.consistency(subject_series),
                assessments=len(subject_series),
            )
        )
    return summaries


def student_assessment_series(
    db: Session, student: Student, filters: FilterParams, scope: AccessScope
) -> dict[str, object]:
    """Chronological results for one student with the class average alongside each."""
    student_filters = filters.model_copy(update={"student_id": student.id})
    own_facts = fetch_facts(db, student_filters, scope, graded_only=False)
    if not own_facts:
        return {"assessments": [], "class_averages": {}}

    # Every assessment id here came from a result the scope already authorised, and an
    # assessment belongs to exactly one section, so averaging all of its scores yields
    # this student's own class average without widening what the caller can read.
    assessment_ids = [fact.assessment_id for fact in own_facts]
    class_stmt = (
        select(Score.assessment_id, func.avg(PCT_EXPR))
        .select_from(Score)
        .join(Assessment, Score.assessment_id == Assessment.id)
        .where(
            Score.assessment_id.in_(assessment_ids),
            Score.is_absent.is_(False),
            Score.marks_obtained.is_not(None),
        )
        .group_by(Score.assessment_id)
    )
    class_averages = {
        assessment_id: round(avg, 2) if avg is not None else None
        for assessment_id, avg in db.execute(class_stmt).all()
    }
    return {"assessments": own_facts, "class_averages": class_averages}


def student_term_progress(
    db: Session, student: Student, filters: FilterParams, scope: AccessScope
) -> list[dict[str, object]]:
    """Weighted term averages for the student next to their section's average."""
    scope.assert_student(student.id)
    # The whole year is always shown here, so a term filter must not truncate it.
    year_filters = filters.model_copy(update={"term_id": None})

    own_stmt = _base_select(
        Assessment.term_id,
        Term.name,
        Term.sequence,
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        filters=year_filters.model_copy(update={"student_id": student.id}),
        scope=scope,
    ).group_by(Assessment.term_id, Term.name, Term.sequence)

    term_meta: dict[int, tuple[str, int]] = {}
    own_averages: dict[int, float] = {}
    for term_id, name, sequence, avg in db.execute(own_stmt).all():
        term_meta[term_id] = (name, sequence)
        if avg is not None:
            own_averages[term_id] = round(avg, 2)

    cohort_by_term = _cohort_averages_by_dimension(
        db, year_filters, scope, student.section_id, Assessment.term_id
    )

    rows: list[dict[str, object]] = []
    previous: float | None = None
    for term_id, (term_name, sequence) in sorted(
        term_meta.items(), key=lambda item: item[1][1]
    ):
        student_avg = own_averages.get(term_id)
        cohort = cohort_by_term.get(term_id, [])
        rank, cohort_size, percentile = metrics.rank_and_percentile(student_avg, cohort)
        rows.append(
            {
                "term_id": term_id,
                "term": term_name,
                "sequence": sequence,
                "student_avg": student_avg,
                "class_avg": metrics.safe_mean(cohort),
                "rank": rank,
                "cohort": cohort_size,
                "percentile": percentile,
                "delta": metrics.delta(student_avg, previous),
                "grade": metrics.letter_grade(student_avg),
            }
        )
        previous = student_avg
    return rows


def student_remarks(
    db: Session, student_id: int, filters: FilterParams
) -> list[dict[str, object]]:
    stmt = (
        select(Remark, Term.name, User.full_name, Subject.name)
        .outerjoin(Term, Remark.term_id == Term.id)
        .outerjoin(Teacher, Remark.teacher_id == Teacher.id)
        .outerjoin(User, Teacher.user_id == User.id)
        .outerjoin(Subject, Remark.subject_id == Subject.id)
        .where(Remark.student_id == student_id)
        .order_by(Remark.created_at.desc(), Remark.id.desc())
    )
    if filters.term_id is not None:
        stmt = stmt.where(Remark.term_id == filters.term_id)

    rows: list[dict[str, object]] = []
    for remark, term_name, teacher_name, subject_name in db.execute(stmt).all():
        rows.append(
            {
                "id": remark.id,
                "category": remark.category.label,
                "category_key": remark.category.value,
                "body": remark.body,
                "term": term_name or "--",
                "teacher": teacher_name or "School office",
                "subject": subject_name or "General",
            }
        )
    return rows


def student_overview(
    db: Session, student: Student, filters: FilterParams, scope: AccessScope
) -> dict[str, object]:
    """Headline numbers for a single student."""
    summaries = student_subject_summaries(db, student, filters, scope)
    subject_averages = {s.subject: s.student_avg for s in summaries}
    overall = metrics.safe_mean([s.student_avg for s in summaries])
    class_overall = metrics.safe_mean([s.class_avg for s in summaries])
    failing = sum(
        1
        for s in summaries
        if s.student_avg is not None and s.student_avg < metrics.settings.pass_percentage
    )

    terms = student_term_progress(db, student, filters, scope)
    term_series = [t["student_avg"] for t in terms]
    term_slope = metrics.trend_slope(term_series)

    attendance = attendance_by_student(db, [student.id], filters, scope).get(
        student.id, {}
    )
    attendance_pct = attendance.get("percentage")

    latest_rank = next(
        (t["rank"] for t in reversed(terms) if t["rank"] is not None), None
    )
    cohort = next((t["cohort"] for t in reversed(terms) if t["cohort"]), None)
    strengths, weaknesses = metrics.strength_profile(subject_averages)

    return {
        "student": student,
        "overall": overall,
        "class_overall": class_overall,
        "grade": metrics.letter_grade(overall),
        "descriptor": metrics.grade_descriptor(overall),
        "gap_to_class": metrics.delta(overall, class_overall),
        "term_slope": term_slope,
        "trend": metrics.term_trend_label(term_slope),
        "trend_tone": metrics.term_trend_tone(term_slope),
        "consistency": metrics.consistency(
            [s.student_avg for s in summaries]
        ),
        "attendance": attendance_pct,
        "attendance_detail": attendance,
        "rank": latest_rank,
        "cohort": cohort,
        "failing_subjects": failing,
        "subjects": summaries,
        "terms": terms,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "risk": metrics.assess_risk(overall, term_slope, attendance_pct, failing),
        "assessment_count": sum(s.assessments for s in summaries),
    }


# --------------------------------------------------------------------------- #
# Cohort level analytics (classroom, grade, school)
# --------------------------------------------------------------------------- #


def student_standings(
    db: Session, filters: FilterParams, scope: AccessScope, limit: int | None = None
) -> list[StudentStanding]:
    """Overall average, trajectory, and attendance for every student in scope."""
    scope.assert_identifiable()
    overall_stmt = _base_select(
        Score.student_id,
        Student.full_name,
        Student.roll_no,
        Section.id.label("section_id"),
        Grade.name.label("grade_name"),
        Section.name.label("section_name"),
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        func.count(func.distinct(Assessment.subject_id)).label("subject_count"),
        filters=filters,
        scope=scope,
    ).group_by(
        Score.student_id,
        Student.full_name,
        Student.roll_no,
        Section.id,
        Grade.name,
        Section.name,
    )

    overall: dict[int, dict[str, object]] = {}
    for row in db.execute(overall_stmt).all():
        overall[row.student_id] = {
            "name": row.full_name,
            "roll_no": row.roll_no,
            "section_id": row.section_id,
            "section_label": f"{row.grade_name} - {row.section_name}",
            "grade_name": row.grade_name,
            "average": round(row.weighted_avg, 2) if row.weighted_avg is not None else None,
            "subjects_assessed": row.subject_count,
        }
    if not overall:
        return []

    # Term averages give a cheap, meaningful trajectory (one point per term).
    term_stmt = _base_select(
        Score.student_id,
        Term.sequence,
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        filters=filters,
        scope=scope,
    ).group_by(Score.student_id, Term.sequence)
    series: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for student_id, sequence, avg in db.execute(term_stmt).all():
        if avg is not None:
            series[student_id].append((sequence, avg))

    # Subject averages identify how many subjects a student is failing.
    subject_stmt = _base_select(
        Score.student_id,
        Assessment.subject_id,
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        filters=filters,
        scope=scope,
    ).group_by(Score.student_id, Assessment.subject_id)
    failing: dict[int, int] = defaultdict(int)
    for student_id, _subject_id, avg in db.execute(subject_stmt).all():
        if avg is not None and avg < metrics.settings.pass_percentage:
            failing[student_id] += 1

    attendance = attendance_by_student(db, list(overall), filters, scope)

    standings: list[StudentStanding] = []
    for student_id, info in overall.items():
        ordered = [value for _seq, value in sorted(series.get(student_id, []))]
        standings.append(
            StudentStanding(
                student_id=student_id,
                name=str(info["name"]),
                roll_no=int(info["roll_no"]),
                section_id=int(info["section_id"]),
                section_label=str(info["section_label"]),
                grade_name=str(info["grade_name"]),
                average=info["average"],  # type: ignore[arg-type]
                term_slope=metrics.trend_slope(ordered),
                attendance=attendance.get(student_id, {}).get("percentage"),
                failing_subjects=failing.get(student_id, 0),
                subjects_assessed=int(info["subjects_assessed"]),
            )
        )

    standings.sort(key=lambda s: (s.average is None, -(s.average or 0)))
    return standings[:limit] if limit else standings


def grouped_averages(
    db: Session,
    filters: FilterParams,
    scope: AccessScope,
    group: str,
) -> list[dict[str, object]]:
    """Weighted average, pass rate, and volume grouped by one dimension."""
    dimensions = {
        "subject": (Subject.id, Subject.name),
        "grade": (Grade.id, Grade.name),
        "section": (Section.id, Section.name),
        "term": (Term.id, Term.name),
        "assessment_type": (Assessment.assessment_type, Assessment.assessment_type),
    }
    if group not in dimensions:
        raise ValueError(f"Unsupported grouping: {group}")
    id_col, label_col = dimensions[group]

    passed = case((PCT_EXPR >= metrics.settings.pass_percentage, 1), else_=0)
    stmt = _base_select(
        id_col.label("group_id"),
        label_col.label("group_label"),
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        func.avg(PCT_EXPR).label("plain_avg"),
        func.min(PCT_EXPR).label("lowest"),
        func.max(PCT_EXPR).label("highest"),
        func.count(Score.id).label("results"),
        func.sum(passed).label("passed"),
        func.count(func.distinct(Score.student_id)).label("students"),
        filters=filters,
        scope=scope,
    ).group_by(id_col, label_col)

    if group == "section":
        stmt = stmt.add_columns(Grade.name.label("grade_name")).group_by(Grade.name)
    if group == "grade":
        stmt = stmt.add_columns(Grade.level.label("grade_level")).group_by(Grade.level)
    if group == "term":
        stmt = stmt.add_columns(Term.sequence.label("term_sequence")).group_by(
            Term.sequence
        )

    # Grades and terms read best in their natural progression; the remaining
    # dimensions are comparisons, so those stay ranked by average.
    natural_order: dict[object, int] = {}
    rows: list[dict[str, object]] = []
    for row in db.execute(stmt).all():
        mapping = row._mapping
        label = mapping["group_label"]
        if group == "assessment_type":
            label = (
                label.label if isinstance(label, AssessmentType) else str(label)
            )
            group_id = (
                mapping["group_id"].value
                if isinstance(mapping["group_id"], AssessmentType)
                else str(mapping["group_id"])
            )
        else:
            group_id = mapping["group_id"]
        if group == "section":
            label = f"{mapping['grade_name']} - {label}"
        if group == "grade":
            natural_order[group_id] = mapping["grade_level"]
        if group == "term":
            natural_order[group_id] = mapping["term_sequence"]

        results = mapping["results"] or 0
        rows.append(
            {
                "id": group_id,
                "label": label,
                "average": round(mapping["weighted_avg"], 2)
                if mapping["weighted_avg"] is not None
                else None,
                "lowest": round(mapping["lowest"], 2)
                if mapping["lowest"] is not None
                else None,
                "highest": round(mapping["highest"], 2)
                if mapping["highest"] is not None
                else None,
                "results": results,
                "students": mapping["students"] or 0,
                "pass_rate": round((mapping["passed"] or 0) / results * 100, 1)
                if results
                else None,
            }
        )

    if natural_order:
        rows.sort(key=lambda r: natural_order.get(r["id"], 0))
    else:
        rows.sort(key=lambda r: (r["average"] is None, -(r["average"] or 0)))
    return rows


def assessment_averages(
    db: Session, filters: FilterParams, scope: AccessScope
) -> list[dict[str, object]]:
    """Per-assessment class average, in chronological order, with a difficulty read."""
    stmt = _base_select(
        Assessment.id.label("assessment_id"),
        Assessment.name.label("assessment_name"),
        Assessment.assessment_type,
        Assessment.conducted_on,
        Subject.name.label("subject_name"),
        func.avg(PCT_EXPR).label("avg_pct"),
        func.min(PCT_EXPR).label("lowest"),
        func.max(PCT_EXPR).label("highest"),
        func.count(Score.id).label("graded"),
        filters=filters,
        scope=scope,
    ).group_by(
        Assessment.id,
        Assessment.name,
        Assessment.assessment_type,
        Assessment.conducted_on,
        Subject.name,
    ).order_by(Assessment.conducted_on, Assessment.id)

    rows: list[dict[str, object]] = []
    for row in db.execute(stmt).all():
        avg = round(row.avg_pct, 2) if row.avg_pct is not None else None
        rows.append(
            {
                "id": row.assessment_id,
                "label": row.assessment_name,
                "short_label": f"{row.subject_name[:4]} {row.assessment_type.label}",
                "subject": row.subject_name,
                "type": row.assessment_type.label,
                "date": row.conducted_on.isoformat(),
                "average": avg,
                "lowest": round(row.lowest, 2) if row.lowest is not None else None,
                "highest": round(row.highest, 2) if row.highest is not None else None,
                "graded": row.graded,
                "difficulty": metrics.difficulty_index(avg),
            }
        )
    return rows


def score_values(db: Session, filters: FilterParams, scope: AccessScope) -> list[float]:
    """Every individual result percentage in scope, for distributions and pass rates."""
    stmt = _base_select(PCT_EXPR.label("pct"), filters=filters, scope=scope)
    return [round(row.pct, 2) for row in db.execute(stmt).all() if row.pct is not None]


def matrix_averages(
    db: Session,
    filters: FilterParams,
    scope: AccessScope,
    row_dimension: str,
    column_dimension: str = "subject",
) -> dict[str, object]:
    """A two-dimensional average matrix, used for the heatmaps."""
    # Each dimension carries its own ordering columns, so grade 10 sorts after
    # grade 6 and assessments stay chronological rather than alphabetical.
    dimensions = {
        "subject": (Subject.id, Subject.name, (Subject.name,)),
        "section": (Section.id, Section.name, (Grade.level, Section.name)),
        "grade": (Grade.id, Grade.name, (Grade.level,)),
        "student": (Student.id, Student.full_name, (Student.roll_no,)),
        "term": (Term.id, Term.name, (Term.sequence,)),
        "assessment": (
            Assessment.id,
            Assessment.name,
            (Assessment.conducted_on, Assessment.name),
        ),
    }
    if row_dimension not in dimensions or column_dimension not in dimensions:
        raise ValueError("Unsupported matrix dimension")
    if "student" in (row_dimension, column_dimension):
        scope.assert_identifiable()

    row_id, row_label, row_sort = dimensions[row_dimension]
    col_id, col_label, col_sort = dimensions[column_dimension]

    ordering = [
        expr.label(f"row_sort_{index}") for index, expr in enumerate(row_sort)
    ] + [expr.label(f"col_sort_{index}") for index, expr in enumerate(col_sort)]
    if row_dimension == "section":
        ordering.append(Grade.name.label("row_prefix"))

    stmt = _base_select(
        row_id.label("row_id"),
        row_label.label("row_label"),
        col_id.label("col_id"),
        col_label.label("col_label"),
        *ordering,
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        filters=filters,
        scope=scope,
    ).group_by(row_id, row_label, col_id, col_label, *ordering)

    row_labels: dict[int, str] = {}
    row_order: dict[int, tuple] = {}
    col_labels: dict[int, str] = {}
    col_order: dict[int, tuple] = {}
    values: dict[tuple[int, int], float] = {}

    def sort_key(mapping: Mapping, prefix: str, count: int) -> tuple:
        # None sorts last within each position rather than raising on comparison.
        return tuple(
            (mapping[f"{prefix}_sort_{index}"] is None, mapping[f"{prefix}_sort_{index}"])
            for index in range(count)
        )

    for row in db.execute(stmt).all():
        mapping = row._mapping
        r_id, c_id = mapping["row_id"], mapping["col_id"]
        label = str(mapping["row_label"])
        if row_dimension == "section":
            label = f"{mapping['row_prefix']} - {label}"
        row_labels[r_id] = label
        row_order[r_id] = sort_key(mapping, "row", len(row_sort))
        col_labels[c_id] = str(mapping["col_label"])
        col_order[c_id] = sort_key(mapping, "col", len(col_sort))
        if mapping["weighted_avg"] is not None:
            values[(r_id, c_id)] = round(mapping["weighted_avg"], 2)

    sorted_rows = sorted(row_labels, key=lambda k: row_order[k])
    sorted_cols = sorted(col_labels, key=lambda k: col_order[k])

    return {
        "rows": [{"id": r, "label": row_labels[r]} for r in sorted_rows],
        "columns": [{"id": c, "label": col_labels[c]} for c in sorted_cols],
        "values": [
            [values.get((r, c)) for c in sorted_cols] for r in sorted_rows
        ],
    }


def cohort_overview(
    db: Session, filters: FilterParams, scope: AccessScope
) -> dict[str, object]:
    """Headline numbers for a classroom, a grade, or the whole school."""
    stmt = _base_select(
        WEIGHTED_AVG_EXPR.label("weighted_avg"),
        func.count(Score.id).label("results"),
        func.count(func.distinct(Score.student_id)).label("students"),
        func.count(func.distinct(Assessment.id)).label("assessments"),
        func.count(func.distinct(Assessment.subject_id)).label("subjects"),
        func.count(func.distinct(Assessment.section_id)).label("sections"),
        filters=filters,
        scope=scope,
    )
    row = db.execute(stmt).one()

    values = score_values(db, filters, scope)
    standings = student_standings(db, filters, scope)
    attendance = attendance_by_student(db, None, filters, scope)
    attendance_values = [
        info["percentage"]
        for info in attendance.values()
        if info.get("percentage") is not None
    ]

    at_risk = [s for s in standings if s.risk.is_at_risk]
    high_risk = [s for s in standings if s.risk.level == "high"]
    absent_stmt = _base_select(
        func.count(Score.id),
        filters=filters,
        scope=scope,
        graded_only=False,
    ).where(Score.is_absent.is_(True))
    absent_count = db.execute(absent_stmt).scalar() or 0

    return {
        "average": round(row.weighted_avg, 2) if row.weighted_avg is not None else None,
        "results": row.results or 0,
        "students": row.students or 0,
        "assessments": row.assessments or 0,
        "subjects": row.subjects or 0,
        "sections": row.sections or 0,
        "pass_rate": metrics.pass_rate(values),
        "median": round(sorted(values)[len(values) // 2], 2) if values else None,
        "highest": max(values) if values else None,
        "lowest": min(values) if values else None,
        "spread": metrics.consistency(values),
        "absent_count": absent_count,
        "attendance": metrics.safe_mean(attendance_values),
        "at_risk_count": len(at_risk),
        "high_risk_count": len(high_risk),
        "toppers": standings[:5],
        "needs_attention": sorted(
            at_risk, key=lambda s: (s.average is None, s.average or 0)
        )[:5],
        "standings": standings,
    }


def teacher_effectiveness(
    db: Session, filters: FilterParams, scope: AccessScope
) -> list[dict[str, object]]:
    """Average result per teacher, across the class and subject pairings they own."""
    passed = case((PCT_EXPR >= metrics.settings.pass_percentage, 1), else_=0)
    stmt = (
        _base_select(
            Teacher.id.label("teacher_id"),
            User.full_name.label("teacher_name"),
            Teacher.department,
            WEIGHTED_AVG_EXPR.label("weighted_avg"),
            func.count(Score.id).label("results"),
            func.sum(passed).label("passed"),
            func.count(func.distinct(Score.student_id)).label("students"),
            func.count(func.distinct(Assessment.section_id)).label("sections"),
            func.count(func.distinct(Assessment.subject_id)).label("subjects"),
            filters=filters,
            scope=scope,
        )
        .join(
            TeacherAssignment,
            and_(
                TeacherAssignment.section_id == Assessment.section_id,
                TeacherAssignment.subject_id == Assessment.subject_id,
            ),
        )
        .join(Teacher, TeacherAssignment.teacher_id == Teacher.id)
        .join(User, Teacher.user_id == User.id)
        .group_by(Teacher.id, User.full_name, Teacher.department)
    )

    rows: list[dict[str, object]] = []
    for row in db.execute(stmt).all():
        results = row.results or 0
        rows.append(
            {
                "teacher_id": row.teacher_id,
                "teacher": row.teacher_name,
                "department": row.department or "--",
                "average": round(row.weighted_avg, 2)
                if row.weighted_avg is not None
                else None,
                "pass_rate": round((row.passed or 0) / results * 100, 1)
                if results
                else None,
                "results": results,
                "students": row.students or 0,
                "sections": row.sections or 0,
                "subjects": row.subjects or 0,
            }
        )
    rows.sort(key=lambda r: (r["average"] is None, -(r["average"] or 0)))
    return rows


def attendance_vs_performance(
    db: Session, filters: FilterParams, scope: AccessScope
) -> list[dict[str, object]]:
    """Scatter points pairing each student's attendance with their average."""
    standings = student_standings(db, filters, scope)
    points: list[dict[str, object]] = []
    for standing in standings:
        if standing.attendance is None or standing.average is None:
            continue
        points.append(
            {
                "x": standing.attendance,
                "y": standing.average,
                "label": standing.name,
                "section": standing.section_label,
                "risk": standing.risk.level,
            }
        )
    return points


def default_student(
    db: Session, filters: FilterParams, scope: AccessScope
) -> Student | None:
    """The student to open on when staff have not picked one yet.

    Honours the grade and section filters so the choice follows the filter bar,
    and never reaches outside the caller's scope.
    """
    stmt = (
        select(Student)
        .join(Section, Student.section_id == Section.id)
        .join(Grade, Section.grade_id == Grade.id)
        .where(Student.is_active.is_(True))
        .order_by(Grade.level, Section.name, Student.roll_no)
        .limit(1)
    )
    if scope.student_ids is not None:
        stmt = stmt.where(Student.id.in_(scope.student_ids or {-1}))
    if scope.section_ids is not None:
        stmt = stmt.where(Student.section_id.in_(scope.section_ids or {-1}))
    if filters.section_id is not None:
        stmt = stmt.where(Student.section_id == filters.section_id)
    if filters.grade_id is not None:
        stmt = stmt.where(Section.grade_id == filters.grade_id)
    year_id = filters.academic_year_id or scope.academic_year_id
    if year_id is not None:
        stmt = stmt.where(Section.academic_year_id == year_id)
    return db.scalars(stmt).first()


def section_label(db: Session, section_id: int | None) -> str:
    """Human label for one classroom, for example "Grade 8 - A"."""
    if section_id is None:
        return "This class"
    row = db.execute(
        select(Grade.name, Section.name)
        .join(Grade, Section.grade_id == Grade.id)
        .where(Section.id == section_id)
    ).first()
    return f"{row[0]} - {row[1]}" if row else "This class"


def default_section_id(
    db: Session, filters: FilterParams, scope: AccessScope
) -> int | None:
    """The classroom to show when the caller has not picked one.

    A teacher falls back to their own first section. A principal has no home
    classroom, so pick the lowest grade and section they are currently filtered
    to, which keeps the grade filter meaningful on classroom pages.
    """
    if scope.section_ids is not None:
        return sorted(scope.section_ids)[0] if scope.section_ids else None

    stmt = (
        select(Section.id)
        .join(Grade, Section.grade_id == Grade.id)
        .order_by(Grade.level, Section.name)
        .limit(1)
    )
    if filters.grade_id is not None:
        stmt = stmt.where(Section.grade_id == filters.grade_id)
    year_id = filters.academic_year_id or scope.academic_year_id
    if year_id is not None:
        stmt = stmt.where(Section.academic_year_id == year_id)
    return db.scalars(stmt).first()


# --------------------------------------------------------------------------- #
# Filter options
# --------------------------------------------------------------------------- #


def filter_options(db: Session, scope: AccessScope) -> FilterOptions:
    """Dropdown contents, already narrowed to what the caller may select."""
    years = [
        FilterOption(id=year.id, label=year.label)
        for year in db.scalars(
            select(AcademicYear).order_by(AcademicYear.start_date.desc())
        )
    ]

    term_stmt = select(Term).order_by(Term.sequence)
    if scope.academic_year_id is not None:
        term_stmt = term_stmt.where(Term.academic_year_id == scope.academic_year_id)
    terms = [FilterOption(id=t.id, label=t.name) for t in db.scalars(term_stmt)]

    subject_stmt = select(Subject).order_by(Subject.name)
    if scope.subject_ids is not None:
        subject_stmt = subject_stmt.where(Subject.id.in_(scope.subject_ids or {-1}))
    subjects = [FilterOption(id=s.id, label=s.name) for s in db.scalars(subject_stmt)]

    section_stmt = (
        select(Section, Grade)
        .join(Grade, Section.grade_id == Grade.id)
        .order_by(Grade.level, Section.name)
    )
    if scope.section_ids is not None:
        section_stmt = section_stmt.where(Section.id.in_(scope.section_ids or {-1}))
    if scope.academic_year_id is not None:
        section_stmt = section_stmt.where(
            Section.academic_year_id == scope.academic_year_id
        )
    section_rows = db.execute(section_stmt).all()
    sections = [
        FilterOption(id=section.id, label=f"{grade.name} - {section.name}")
        for section, grade in section_rows
    ]

    grade_ids = {grade.id for _section, grade in section_rows}
    grade_stmt = select(Grade).order_by(Grade.level)
    if scope.section_ids is not None:
        grade_stmt = grade_stmt.where(Grade.id.in_(grade_ids or {-1}))
    grades = [FilterOption(id=g.id, label=g.name) for g in db.scalars(grade_stmt)]

    students: list[FilterOption] = []
    if scope.is_admin:
        student_stmt = (
            select(Student.id, Student.full_name, Grade.name, Section.name)
            .join(Section, Student.section_id == Section.id)
            .join(Grade, Section.grade_id == Grade.id)
            .where(Student.is_active.is_(True))
            .order_by(Grade.level, Section.name, Student.roll_no)
        )
        students = [
            FilterOption(id=sid, label=f"{name} ({grade_name} - {section_name})")
            for sid, name, grade_name, section_name in db.execute(student_stmt).all()
        ]
    else:
        for student in scope.students:
            students.append(FilterOption(id=student.id, label=student.full_name))

    return FilterOptions(
        academic_years=years,
        terms=terms,
        subjects=subjects,
        grades=grades,
        sections=sections,
        students=students,
        assessment_types=[
            FilterOption(id=t.value, label=t.label) for t in AssessmentType
        ],
    )
