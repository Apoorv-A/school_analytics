"""Principal / administrator portal: the whole school, with drill-down anywhere."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role, Student
from app.portals import render_dashboard, student_subtitle
from app.routers.student_views import STUDENT_PAGES
from app.schemas import FilterParams, filter_params

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_roles(Role.ADMIN))],
)

SCHOOL_FILTERS = [
    "academic_year",
    "term",
    "grade",
    "section",
    "subject",
    "assessment_type",
]

PAGES: dict[str, dict] = {
    "overview": {
        "title": "School overview",
        "subtitle": "Every grade, class, and subject in one place",
        "filters": SCHOOL_FILTERS,
        "cards": [
            {"key": "school.kpis", "title": "School at a glance", "span": 12, "holder": "state"},
            {
                "key": "school.term_trend",
                "title": "Term trend by grade",
                "subtitle": "Ignores the term filter so the trajectory stays visible.",
                "span": 8,
                "holder": "chart-holder chart-holder--tall",
            },
            {
                "key": "school.pass_rate",
                "title": "Pass rate",
                "span": 4,
                "holder": "chart-holder chart-holder--tall",
            },
            {"key": "school.grade_averages", "title": "Performance by grade", "span": 6},
            {"key": "school.subject_averages", "title": "Performance by subject", "span": 6},
            {
                "key": "school.distribution",
                "title": "Score distribution across the school",
                "span": 6,
            },
            {
                "key": "school.assessment_type_averages",
                "title": "Performance by assessment type",
                "span": 6,
            },
        ],
    },
    "grades": {
        "title": "Grades",
        "subtitle": "Compare year groups and watch them move across terms",
        "filters": SCHOOL_FILTERS,
        "cards": [
            {"key": "school.grade_averages", "title": "Average and pass rate by grade", "span": 12},
            {
                "key": "school.term_trend",
                "title": "Term trend by grade",
                "span": 12,
                "holder": "chart-holder chart-holder--tall",
            },
            {
                "key": "school.section_matrix",
                "title": "Class by subject heatmap",
                "subtitle": "Spot the exact class and subject pairing that needs help.",
                "span": 12,
                "holder": "state",
            },
        ],
    },
    "subjects": {
        "title": "Subjects",
        "subtitle": "Which subjects carry the school and which drag it down",
        "filters": SCHOOL_FILTERS,
        "cards": [
            {
                "key": "school.subject_averages",
                "title": "Average, lowest, and highest by subject",
                "span": 12,
            },
            {
                "key": "school.assessment_type_averages",
                "title": "Average by assessment type",
                "span": 6,
            },
            {"key": "school.distribution", "title": "Score distribution", "span": 6},
            {
                "key": "school.section_matrix",
                "title": "Class by subject heatmap",
                "span": 12,
                "holder": "state",
            },
        ],
    },
    "classes": {
        "title": "Classes",
        "subtitle": "Rank every classroom and see where the gaps are",
        "filters": SCHOOL_FILTERS,
        "cards": [
            {
                "key": "school.section_leaderboard",
                "title": "Class leaderboard",
                "subtitle": "Sortable. Click a column heading to reorder.",
                "span": 12,
                "holder": "state",
            },
            {
                "key": "school.section_matrix",
                "title": "Class by subject heatmap",
                "span": 12,
                "holder": "state",
            },
        ],
    },
    "attention": {
        "title": "Students at risk",
        "subtitle": "The school-wide cohort that needs intervention",
        "filters": SCHOOL_FILTERS,
        "cards": [
            {
                "key": "school.at_risk",
                "title": "Flagged students",
                "subtitle": "High risk first. Reasons are listed per student.",
                "span": 12,
                "holder": "state",
            },
            {
                "key": "school.attendance_scatter",
                "title": "Attendance against performance",
                "subtitle": "Red is high risk, amber needs watching, green is on track.",
                "span": 12,
                "holder": "chart-holder chart-holder--tall",
            },
        ],
    },
    "teachers": {
        "title": "Teaching outcomes",
        "subtitle": "Average results across the classes and subjects each teacher owns",
        "filters": SCHOOL_FILTERS,
        "cards": [
            {
                "key": "school.teacher_effectiveness",
                "title": "Results by teacher",
                "subtitle": "Compare within a subject or grade before drawing conclusions.",
                "span": 12,
                "holder": "state",
            },
            {"key": "school.subject_averages", "title": "Subject context", "span": 12},
        ],
    },
    "student": {
        "title": "Student lookup",
        "subtitle": "Pick any student to open their full record",
        "filters": ["academic_year", "student", "term", "subject", "assessment_type"],
        "cards": STUDENT_PAGES["overview"]["cards"],
    },
}


def _render(
    request: Request,
    page_key: str,
    db: Session,
    scope: AccessScope,
    filters: FilterParams,
) -> Response:
    page = PAGES[page_key]
    return render_dashboard(
        request,
        db,
        scope,
        filters,
        title=page["title"],
        subtitle=page.get("subtitle"),
        active_nav="/admin" if page_key == "overview" else f"/admin/{page_key}",
        visible_filters=page["filters"],
        cards=page["cards"],
        intro=page.get("intro"),
    )


@router.get("")
def overview(
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    filters: FilterParams = Depends(filter_params),
) -> Response:
    return _render(request, "overview", db, scope, filters)


# `target_id`, not `student_id`, because `filter_params` already supplies a
# `student_id` query parameter and the two cannot share a name.
@router.get("/student/{target_id}")
def student_detail(
    request: Request,
    target_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    filters: FilterParams = Depends(filter_params),
) -> Response:
    student = db.get(Student, target_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )
    return render_dashboard(
        request,
        db,
        scope,
        filters.with_student(student.id),
        title=f"Student detail - {student.full_name}",
        subtitle=student_subtitle(student),
        active_nav="/admin/student",
        visible_filters=["academic_year", "student", "term", "subject", "assessment_type"],
        cards=STUDENT_PAGES["overview"]["cards"],
    )


@router.get("/{page_key}")
def page(
    request: Request,
    page_key: str = Path(pattern=r"^[a-z]{1,24}$"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    filters: FilterParams = Depends(filter_params),
) -> Response:
    if page_key not in PAGES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Page not found."
        )
    return _render(request, page_key, db, scope, filters)
