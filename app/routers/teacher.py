"""Teacher portal: classroom analytics, plus a drill-down into any student they teach."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy.orm import Session

from app.analytics.queries import filter_options
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role, Student
from app.portals import render_dashboard, student_subtitle
from app.routers.student_views import STUDENT_PAGES
from app.schemas import FilterParams, filter_params

router = APIRouter(
    prefix="/teacher",
    tags=["teacher"],
    dependencies=[Depends(require_roles(Role.TEACHER))],
)

CLASSROOM_FILTERS = [
    "academic_year",
    "section",
    "section_required",
    "term",
    "subject",
    "assessment_type",
]

PAGES: dict[str, dict] = {
    "overview": {
        "title": "Classroom overview",
        "filters": CLASSROOM_FILTERS,
        "cards": [
            {
                "key": "section.kpis",
                "title": "How the class is doing",
                "span": 12,
                "holder": "state",
            },
            {
                "key": "section.assessment_averages",
                "title": "Average by assessment",
                "subtitle": "Class average with the highest and lowest result on each paper.",
                "span": 8,
                "holder": "chart-holder chart-holder--tall",
            },
            {
                "key": "section.grade_mix",
                "title": "Grade distribution",
                "span": 4,
                "holder": "chart-holder chart-holder--tall",
            },
            {"key": "section.subject_averages", "title": "Average by subject", "span": 6},
            {
                "key": "section.distribution",
                "title": "Score distribution",
                "subtitle": "How many results fall in each band.",
                "span": 6,
            },
        ],
    },
    "assessments": {
        "title": "Assessment analysis",
        "filters": [*CLASSROOM_FILTERS, "dates"],
        "cards": [
            {
                "key": "section.assessment_averages",
                "title": "Average by assessment",
                "span": 12,
                "holder": "chart-holder chart-holder--tall",
            },
            {
                "key": "section.distribution",
                "title": "Score distribution",
                "span": 6,
            },
            {"key": "section.grade_mix", "title": "Grade mix", "span": 6},
        ],
    },
    "subjects": {
        "title": "Subject comparison",
        "filters": CLASSROOM_FILTERS,
        "cards": [
            {
                "key": "section.subject_averages",
                "title": "Average and pass rate by subject",
                "span": 12,
            },
            {
                "key": "section.heatmap",
                "title": "Student by subject heatmap",
                "subtitle": "Warmer is stronger. Blank means not assessed under these filters.",
                "span": 12,
                "holder": "state",
            },
        ],
    },
    "students": {
        "title": "Students",
        "filters": CLASSROOM_FILTERS,
        "cards": [
            {
                "key": "section.students_table",
                "title": "Every student in this class",
                "subtitle": "Sortable. Click a column heading to reorder.",
                "span": 12,
                "holder": "state",
            },
            {
                "key": "section.heatmap",
                "title": "Student by subject heatmap",
                "span": 12,
                "holder": "state",
            },
        ],
    },
    "attention": {
        "title": "Needs attention",
        "filters": CLASSROOM_FILTERS,
        "cards": [
            {
                "key": "section.at_risk",
                "title": "Students to follow up with",
                "subtitle": "Flagged on average, trajectory, failed subjects, or attendance.",
                "span": 12,
                "holder": "state",
            },
            {
                "key": "section.toppers",
                "title": "Top performers",
                "span": 12,
                "holder": "state",
            },
        ],
    },
    "attendance": {
        "title": "Attendance",
        "filters": [*CLASSROOM_FILTERS, "dates"],
        "cards": [
            {
                "key": "section.attendance",
                "title": "Attendance against average score",
                "subtitle": "Each student's attendance next to their weighted average.",
                "span": 12,
                "holder": "chart-holder chart-holder--tall",
            },
            {"key": "section.kpis", "title": "Class summary", "span": 12, "holder": "state"},
        ],
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
    # Classroom views are always about one section; default to the first one the
    # teacher is assigned to rather than silently spanning all of them.
    section_id = scope.resolve_section_id(filters.section_id)
    filters = filters.with_section(section_id)

    section_label = next(
        (
            option.label
            for option in filter_options(db, scope).sections
            if option.id == section_id
        ),
        "My class",
    )
    return render_dashboard(
        request,
        db,
        scope,
        filters,
        title=page["title"],
        subtitle=f"{section_label} - {scope.user.full_name}",
        active_nav="/teacher" if page_key == "overview" else f"/teacher/{page_key}",
        visible_filters=page["filters"],
        cards=page["cards"],
        # Defaulted above when the teacher has not picked a class.
        pinned=["section_id"],
    )


@router.get("")
def overview(
    request: Request,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    filters: FilterParams = Depends(filter_params),
) -> Response:
    return _render(request, "overview", db, scope, filters)


# Named `target_id` rather than `student_id` because `filter_params` already contributes
# a `student_id` query parameter, and FastAPI cannot have the same name in both places.
@router.get("/student/{target_id}")
def student_detail(
    request: Request,
    target_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    filters: FilterParams = Depends(filter_params),
) -> Response:
    """Full student view, limited to students in the teacher's own classes."""
    scope.assert_student(target_id)
    student = db.get(Student, target_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )
    page = STUDENT_PAGES["overview"]
    return render_dashboard(
        request,
        db,
        scope,
        filters.with_student(student.id),
        title=f"Student detail - {student.full_name}",
        subtitle=student_subtitle(student),
        active_nav="/teacher/students",
        visible_filters=["academic_year", "term", "subject", "assessment_type"],
        cards=page["cards"],
        # This page is about the student in the URL, and deliberately offers no
        # student control, so the charts have to be told which one.
        pinned=["student_id"],
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
