"""Shared page assembly for the four portals.

A portal page is a navigation entry plus a list of chart cards. Keeping that
declaration here means adding a view is a small, reviewable change rather than a new
template, and the four portals cannot drift apart in layout or behaviour.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import queries
from app.deps import AccessScope
from app.models import Role, School, Student
from app.schemas import FilterParams
from app.templating import templates

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
            ],
        },
    ],
}


def _school(db: Session) -> School | None:
    return db.scalars(select(School).limit(1)).first()


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
    intro: str | None = None,
    template: str = "dashboard.html",
    extra: dict | None = None,
):
    """Render a portal page with the filter bar and its card grid."""
    context = {
        "page_title": title,
        "page_subtitle": subtitle,
        "active_nav": active_nav,
        "visible_filters": visible_filters,
        "cards": cards,
        "intro": intro,
        "filters": filters,
        "filter_options": queries.filter_options(db, scope),
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
