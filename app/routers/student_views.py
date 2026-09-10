"""Page definitions shared by the parent, student, and staff student-detail views.

The charts a parent sees for their child are exactly the charts a teacher sees for the
same child; only the access scope differs. Defining the pages once keeps them
consistent and means a fix lands everywhere at the same time.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role
from app.portals import render_dashboard, student_subtitle
from app.schemas import FilterParams, filter_params
from app.tenant.features import require_parent_portal, require_student_portal

_INSIGHT_SUMMARY_CARD = {
    "key": "student.insight_summary",
    "title": "Progress summary",
    "subtitle": "A family-friendly snapshot of how things are going.",
    "span": 12,
    "holder": "state",
}

_ADMIN_INSIGHT_CARD = {
    "key": "student.insights",
    "title": "Insight report",
    "subtitle": "Evidence-backed summary with suggested actions.",
    "span": 12,
    "holder": "state",
}

_STUDENT_OVERVIEW_BODY_CARDS: list[dict] = [
            {"key": "student.kpis", "title": "Where things stand", "span": 12, "holder": "state"},
            {
                "key": "student.subject_trend",
                "title": "Scores through the year",
                "subtitle": "Pick a subject in the filter bar to see every assessment.",
                "span": 8,
                "holder": "chart-holder chart-holder--tall",
            },
            {
                "key": "student.subject_radar",
                "title": "Subject strength profile",
                "subtitle": "Compared with the class average.",
                "span": 4,
                "holder": "chart-holder chart-holder--tall",
            },
            {
                "key": "student.vs_class",
                "title": "Versus the class, subject by subject",
                "span": 8,
            },
            {
                "key": "student.grade_mix",
                "title": "Grade spread across subjects",
                "span": 4,
            },
            {
                "key": "student.term_progress",
                "title": "Term on term progress",
                "span": 6,
            },
            {
                "key": "student.attendance",
                "title": "Attendance by month",
                "span": 6,
            },
]


def portal_student_overview_cards() -> list[dict]:
    """Overview cards for parent and student portals (includes insight_summary)."""
    return [_INSIGHT_SUMMARY_CARD, *_STUDENT_OVERVIEW_BODY_CARDS]


def staff_student_overview_cards(*, admin: bool = False) -> list[dict]:
    """Overview cards for staff student views without parent-only charts."""
    cards: list[dict] = []
    if admin:
        cards.append(_ADMIN_INSIGHT_CARD)
    cards.extend(_STUDENT_OVERVIEW_BODY_CARDS)
    return cards


STUDENT_PAGES: dict[str, dict] = {
    "overview": {
        "title": "Performance overview",
        "filters": ["academic_year", "student", "term", "subject"],
        "cards": portal_student_overview_cards(),
    },
    "subjects": {
        "title": "Subject breakdown",
        "filters": ["academic_year", "student", "term", "subject", "assessment_type"],
        "cards": [
            {
                "key": "student.subject_table",
                "title": "Every subject in detail",
                "subtitle": "Average, class comparison, rank, trend, and consistency.",
                "span": 12,
                "holder": "state",
            },
            {"key": "student.vs_class", "title": "You, the class, and the class best", "span": 7},
            {"key": "student.subject_radar", "title": "Strength profile", "span": 5},
        ],
    },
    "assessments": {
        "title": "Assessment history",
        "filters": [
            "academic_year",
            "student",
            "term",
            "subject",
            "assessment_type",
            "dates",
        ],
        "cards": [
            {
                "key": "student.subject_trend",
                "title": "Score timeline",
                "span": 12,
                "holder": "chart-holder chart-holder--tall",
            },
            {
                "key": "student.assessment_table",
                "title": "Every assessment",
                "subtitle": "Sortable. Click any column heading.",
                "span": 12,
                "holder": "state",
            },
        ],
    },
    "progress": {
        "title": "Progress over time",
        "filters": ["academic_year", "student", "subject"],
        "cards": [
            {
                "key": "student.term_progress",
                "title": "Term on term",
                "subtitle": "Weighted average per term against the class.",
                "span": 7,
            },
            {"key": "student.grade_mix", "title": "Grade spread", "span": 5},
            {
                "key": "student.subject_trend",
                "title": "Subject trajectories",
                "span": 12,
                "holder": "chart-holder chart-holder--tall",
            },
        ],
    },
    "attendance": {
        "title": "Attendance",
        "filters": ["academic_year", "student", "term", "dates"],
        "cards": [
            {
                "key": "student.attendance",
                "title": "Month by month",
                "subtitle": "Present, late, excused, and absent days.",
                "span": 12,
                "holder": "chart-holder chart-holder--tall",
            },
            {"key": "student.kpis", "title": "Summary", "span": 12, "holder": "state"},
        ],
    },
    "remarks": {
        "title": "Teacher remarks",
        "filters": ["academic_year", "student", "term"],
        "cards": [
            {
                "key": "student.remarks",
                "title": "What teachers have noted",
                "span": 12,
                "holder": "state",
            },
            {"key": "student.kpis", "title": "Context", "span": 12, "holder": "state"},
        ],
    },
}


def build_student_portal(prefix: str, role: Role, tag: str) -> APIRouter:
    """Create the parent or student portal.

    Both portals show the same pages; the difference is entirely in the access scope,
    which resolves to the guardian's children or to the signed-in student themselves.
    """
    portal_guard = require_parent_portal if role is Role.PARENT else require_student_portal
    router = APIRouter(
        prefix=prefix,
        tags=[tag],
        dependencies=[Depends(require_roles(role)), Depends(portal_guard)],
    )

    def render(
        request: Request,
        page_key: str,
        db: Session,
        scope: AccessScope,
        filters: FilterParams,
    ) -> Response:
        page = STUDENT_PAGES[page_key]
        # The child is resolved through the scope, so a guardian cannot open another
        # family's record by editing the student_id in the query string.
        child = scope.resolve_student(filters.student_id)
        filters = filters.with_student(child.id)

        return render_dashboard(
            request,
            db,
            scope,
            filters,
            title=page["title"],
            subtitle=student_subtitle(child),
            active_nav=prefix if page_key == "overview" else f"{prefix}/{page_key}",
            visible_filters=page["filters"],
            cards=page["cards"],
            # Resolved through the scope above. A guardian with one child gets no
            # student control, so the charts would otherwise receive nothing.
            pinned=["student_id"],
            template="student_portal.html",
            extra={
                "children": [
                    {"id": s.id, "name": s.full_name, "current": s.id == child.id}
                    for s in scope.students
                ],
                "child": child,
                "page_prefix": prefix,
            },
        )

    @router.get("")
    def overview(
        request: Request,
        db: Session = Depends(get_db),
        scope: AccessScope = Depends(get_access_scope),
        filters: FilterParams = Depends(filter_params),
    ) -> Response:
        return render(request, "overview", db, scope, filters)

    @router.get("/{page_key}")
    def page(
        request: Request,
        page_key: str = Path(pattern=r"^[a-z]{1,24}$"),
        db: Session = Depends(get_db),
        scope: AccessScope = Depends(get_access_scope),
        filters: FilterParams = Depends(filter_params),
    ) -> Response:
        # page_key only ever selects from this fixed dictionary of pages.
        if page_key not in STUDENT_PAGES:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Page not found."
            )
        return render(request, page_key, db, scope, filters)

    return router
