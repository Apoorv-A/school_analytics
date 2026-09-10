"""Shared page assembly for the four portals.

A portal page is a navigation entry plus a list of chart cards. Keeping that
declaration here means adding a view is a small, reviewable change rather than a new
template, and the four portals cannot drift apart in layout or behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import queries
from app.deps import AccessScope
from app.models import AssessmentType, Role, School, Student
from app.schemas import FilterOptions, FilterParams
from app.templating import templates
from app.tenant.context import require_tenant_context

PORTAL_HOME: dict[Role, str] = {
    Role.ADMIN: "/admin",
    Role.TEACHER: "/teacher",
    Role.PARENT: "/parent",
    Role.STUDENT: "/student",
}

NAV: dict[Role, list[dict]] = {
    Role.PARENT: [
        {
            "title": "My child",
            "items": [
                {"href": "/parent", "label": "Overview", "icon": "\u25a0"},
                {"href": "/parent/subjects", "label": "Subjects", "icon": "\u25c6"},
                {"href": "/parent/assessments", "label": "Assessments", "icon": "\u2261"},
                {"href": "/parent/progress", "label": "Progress", "icon": "\u2197"},
                {"href": "/parent/attendance", "label": "Attendance", "icon": "\u2713"},
                {"href": "/parent/remarks", "label": "Teacher remarks", "icon": "\u201c"},
            ],
        }
    ],
    Role.STUDENT: [
        {
            "title": "My performance",
            "items": [
                {"href": "/student", "label": "Overview", "icon": "\u25a0"},
                {"href": "/student/subjects", "label": "Subjects", "icon": "\u25c6"},
                {"href": "/student/assessments", "label": "Assessments", "icon": "\u2261"},
                {"href": "/student/progress", "label": "Progress", "icon": "\u2197"},
                {"href": "/student/attendance", "label": "Attendance", "icon": "\u2713"},
                {"href": "/student/remarks", "label": "Remarks", "icon": "\u201c"},
            ],
        }
    ],
    Role.TEACHER: [
        {
            "title": "My classroom",
            "items": [
                {"href": "/teacher", "label": "Overview", "icon": "\u25a0"},
                {"href": "/teacher/assessments", "label": "Assessments", "icon": "\u2261"},
                {"href": "/teacher/subjects", "label": "Subjects", "icon": "\u25c6"},
                {"href": "/teacher/students", "label": "Students", "icon": "\u25cf"},
                {"href": "/teacher/attention", "label": "Needs attention", "icon": "\u26a0"},
                {"href": "/teacher/attendance", "label": "Attendance", "icon": "\u2713"},
            ],
        }
    ],
    Role.ADMIN: [
        {
            "title": "School",
            "items": [
                {"href": "/admin", "label": "Overview", "icon": "\u25a0"},
                {"href": "/admin/grades", "label": "Grades", "icon": "\u25b2"},
                {"href": "/admin/subjects", "label": "Subjects", "icon": "\u25c6"},
                {"href": "/admin/classes", "label": "Classes", "icon": "\u25a3"},
            ],
        },
        {
            "title": "People",
            "items": [
                {"href": "/admin/attention", "label": "Students at risk", "icon": "\u26a0"},
                {"href": "/admin/teachers", "label": "Teaching outcomes", "icon": "\u25cf"},
                {"href": "/admin/student", "label": "Student lookup", "icon": "\u2315"},
                {"href": "/admin/import", "label": "Data import", "icon": "\u2191"},
            ],
        },
    ],
}


# Each filter-bar control and the FilterParams fields it lets the user edit.
FILTER_CONTROLS: dict[str, tuple[str, ...]] = {
    "academic_year": ("academic_year_id",),
    "student": ("student_id",),
    "term": ("term_id",),
    "subject": ("subject_id",),
    "grade": ("grade_id",),
    "section": ("section_id",),
    "assessment_type": ("assessment_type",),
    "dates": ("date_from", "date_to"),
}

_LIST_FILTER_FIELDS = {
    "subject_ids": "subject_id",
    "section_ids": "section_id",
}


def _school(db: Session) -> School | None:
    tenant_ctx = require_tenant_context()
    return db.scalars(
        select(School).where(School.tenant_id == tenant_ctx.tenant_id).limit(1)
    ).first()


def _rendered_filters(visible: list[str], options: FilterOptions) -> list[str]:
    """The controls the filter bar will actually draw.

    A dropdown offering a single choice is noise, so it is dropped. This is the
    only place that decision is made, because whatever is not drawn here has to
    travel to the chart API as a pinned value instead.
    """
    dropped = {
        "academic_year": len(options.academic_years) <= 1,
        "student": len(options.students) <= 1,
    }
    return [name for name in visible if not dropped.get(name, False)]


def _normalize_filter_controls(filters: FilterParams) -> FilterParams:
    """Map legacy multi-id query params onto the single-select filter bar."""
    updates: dict[str, object] = {}
    if filters.subject_id is None and filters.subject_ids:
        if len(filters.subject_ids) == 1:
            updates["subject_id"] = filters.subject_ids[0]
            updates["subject_ids"] = None
    if filters.section_id is None and filters.section_ids:
        if len(filters.section_ids) == 1:
            updates["section_id"] = filters.section_ids[0]
            updates["section_ids"] = None
    if updates:
        return filters.model_copy(update=updates)
    return filters


def _pinned_filters(
    filters: FilterParams, rendered: list[str], declared: Sequence[str]
) -> dict[str, str]:
    """Values the route resolved itself that the user cannot edit on this page.

    The chart API is driven entirely by the filter bar, so a value with no
    control would vanish from every card request and the charts would fall back
    to some other default. On a student drill-down that means a page headed with
    one child showing another child's marks, so the route names what it pinned
    and those values travel as hidden controls.

    Only declared fields are forwarded. Inferring this from "every value without
    a control" would also forward an id left over in the query string from
    another page, silently narrowing every chart with nothing in the filter bar
    to reveal it or clear it.
    """
    unknown = set(declared) - set(FilterParams.model_fields)
    if unknown:
        raise ValueError(f"Unknown pinned filter fields: {sorted(unknown)}")

    editable = {field for name in rendered for field in FILTER_CONTROLS.get(name, ())}
    pinned: dict[str, str] = {}
    for field in declared:
        value = getattr(filters, field, None)
        if field in editable or value is None:
            continue
        if isinstance(value, list):
            if len(value) != 1:
                continue
            target = _LIST_FILTER_FIELDS.get(field, field)
            pinned[target] = str(value[0])
        elif isinstance(value, AssessmentType):
            pinned[field] = value.value
        else:
            pinned[field] = str(value)
    return pinned


def render_dashboard(
    request: Request,
    db: Session,
    scope: AccessScope,
    filters: FilterParams,
    *,
    title: str,
    subtitle: str | None,
    active_nav: str,
    visible_filters: list[str],
    cards: list[dict],
    pinned: Sequence[str] = (),
    intro: str | None = None,
    require_student: bool = False,
    template: str = "dashboard.html",
    extra: dict | None = None,
):
    """Render a portal page with the filter bar and its card grid.

    `pinned` names the `FilterParams` fields this route resolved on the user's
    behalf, so they still reach the chart API when the page draws no control for
    them.
    """
    options = queries.filter_options(db, scope)
    filters = _normalize_filter_controls(filters)
    rendered_filters = _rendered_filters(visible_filters, options)
    context = {
        "page_title": title,
        "page_subtitle": subtitle,
        "active_nav": active_nav,
        "visible_filters": visible_filters,
        "rendered_filters": rendered_filters,
        "pinned_filters": _pinned_filters(filters, rendered_filters, pinned),
        "cards": cards,
        "intro": intro,
        "require_student": require_student,
        "filters": filters,
        "filter_options": options,
        "current_user": scope.user,
        "scope": scope,
        "school": _school(db),
        "nav_groups": NAV[scope.role],
        "portal_home": PORTAL_HOME[scope.role],
    }
    if extra:
        context.update(extra)
    return templates.TemplateResponse(request, template, context)


def student_subtitle(student: Student) -> str:
    section = student.section
    return (
        f"{student.full_name} - {section.grade.name} {section.name} - "
        f"Roll {student.roll_no} - Admission {student.admission_no}"
    )
