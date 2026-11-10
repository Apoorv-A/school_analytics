"""F04: role-aware portal welcome (optional, skippable, non-blocking)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import settings
from app.models import Role, User

WELCOME_COOKIE_NAME = "sa_welcome_v1"
WELCOME_REOPEN_QUERY = "welcome"
_WELCOME_SALT = "portal-welcome-dismiss"
_WELCOME_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class WelcomeStep:
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class WelcomeContent:
    headline: str
    lead: str
    steps: tuple[WelcomeStep, ...]
    finish_label: str


_ROLE_WELCOME: dict[Role, WelcomeContent] = {
    Role.STUDENT: WelcomeContent(
        headline="Your learning dashboard",
        lead=(
            "Track scores, attendance, and teacher feedback for the current year."
        ),
        steps=(
            WelcomeStep(
                "See your subjects",
                "Open each subject to compare your results with the class.",
            ),
            WelcomeStep(
                "Check attendance",
                "Charts use official daily attendance, not period roll call.",
            ),
            WelcomeStep(
                "Read remarks",
                "Published teacher remarks appear when your school shares them.",
            ),
        ),
        finish_label="Go to my dashboard",
    ),
    Role.PARENT: WelcomeContent(
        headline="Supporting your child",
        lead=(
            "View progress summaries for your linked learner—only your family scope."
        ),
        steps=(
            WelcomeStep(
                "Pick your child",
                "Use the student selector to switch between linked children.",
            ),
            WelcomeStep(
                "Understand the numbers",
                "Use Metrics guide or About this chart for plain-language help.",
            ),
            WelcomeStep(
                "Stay informed",
                "Remarks appear only when the school publishes them to guardians.",
            ),
        ),
        finish_label="Continue",
    ),
    Role.TEACHER: WelcomeContent(
        headline="Your classes at a glance",
        lead="Find assigned learners and review charts for your sections.",
        steps=(
            WelcomeStep(
                "Search students",
                "Student lookup lists only pupils in your active assignments.",
            ),
            WelcomeStep(
                "Use filters",
                "Narrow by class or subject; exports respect the same scope.",
            ),
            WelcomeStep(
                "Metrics guide",
                "Open Metrics guide when a KPI needs context.",
            ),
        ),
        finish_label="Open my overview",
    ),
    Role.ADMIN: WelcomeContent(
        headline="School-wide insight",
        lead="Monitor performance and attendance with leader-style filters.",
        steps=(
            WelcomeStep(
                "School overview",
                "Start on the dashboard for term trends and cohort summaries.",
            ),
            WelcomeStep(
                "Drill to a class",
                "Open classroom analytics or student lookup for follow-up.",
            ),
            WelcomeStep(
                "Authoritative metrics",
                "Major charts link to the metrics appendix for definitions.",
            ),
        ),
        finish_label="Enter portal",
    ),
}


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt=_WELCOME_SALT)


def welcome_dismiss_cookie_value(user: User) -> str:
    payload = {
        "uid": user.id,
        "tid": str(user.tenant_id),
        "role": user.role.value,
        "v": _WELCOME_SCHEMA_VERSION,
    }
    return _serializer().dumps(payload)


def welcome_reopen_requested(request: Request) -> bool:
    """True when the user explicitly re-opens the tour (Help → Portal tour)."""
    return request.query_params.get(WELCOME_REOPEN_QUERY) == "1"


def welcome_is_dismissed(request: Request, user: User) -> bool:
    raw = request.cookies.get(WELCOME_COOKIE_NAME, "")
    if not raw:
        return False
    try:
        payload = _serializer().loads(raw)
    except BadSignature:
        return False
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("uid") == user.id
        and payload.get("tid") == str(user.tenant_id)
        and payload.get("role") == user.role.value
        and payload.get("v") == _WELCOME_SCHEMA_VERSION
    )


def set_welcome_dismissed_cookie(response: Response, user: User) -> None:
    secure = (
        settings.session_cookie_secure
        if settings.session_cookie_secure is not None
        else False
    )
    response.set_cookie(
        key=WELCOME_COOKIE_NAME,
        value=welcome_dismiss_cookie_value(user),
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def welcome_content_for_role(role: Role) -> WelcomeContent | None:
    return _ROLE_WELCOME.get(role)


def welcome_context(request: Request, user: User) -> dict[str, object] | None:
    """Return template context for the welcome dialog, or None when hidden."""
    if user.must_change_password:
        return None
    if welcome_is_dismissed(request, user) and not welcome_reopen_requested(request):
        return None
    content = welcome_content_for_role(user.role)
    if content is None:
        return None
    return {
        "show": True,
        "headline": content.headline,
        "lead": content.lead,
        "steps": [{"title": s.title, "body": s.body} for s in content.steps],
        "finish_label": content.finish_label,
        "role_key": user.role.value,
    }


def merge_welcome_into_context(
    request: Request, context: dict, user: User | None
) -> None:
    if user is None:
        return
    bundle = welcome_context(request, user)
    if bundle is not None:
        context["welcome"] = bundle
