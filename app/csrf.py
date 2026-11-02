"""CSRF protection for cookie-authenticated state-changing requests.

Uses a double-submit cookie: a signed token is stored in a readable cookie and must
be echoed in the ``X-CSRF-Token`` header (or ``csrf_token`` form field).
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings
from app.security import secrets_match

logger = logging.getLogger(__name__)

_CSRF_SALT = "school-analytics.csrf.v1"
CSRF_COOKIE_NAME: Final = "sa_csrf"
CSRF_HEADER: Final = "X-CSRF-Token"
CSRF_FORM_FIELD: Final = "csrf_token"

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_EXEMPT_PREFIXES = (
    "/login",
    "/forgot-password",
    "/reset-password",
    "/api/auth/",
    "/api/erp/",
    "/healthz",
    "/readyz",
    "/static/",
    "/favicon.ico",
)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=_CSRF_SALT)


def create_csrf_token() -> str:
    return _serializer().dumps({"v": 1})


def validate_csrf_token(token: str) -> bool:
    if not token:
        return False
    try:
        payload = _serializer().loads(token, max_age=settings.session_max_age)
    except BadSignature:
        return False
    return isinstance(payload, dict) and payload.get("v") == 1


def apply_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=settings.session_max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )


def set_csrf_cookie(response: Response) -> str:
    token = create_csrf_token()
    apply_csrf_cookie(response, token)
    return token


def bind_csrf_token_for_request(request: Request) -> None:
    """Ensure authenticated sessions expose a CSRF token for templates and APIs."""
    if not request.cookies.get(settings.session_cookie_name):
        return
    existing = request.cookies.get(CSRF_COOKIE_NAME, "")
    if validate_csrf_token(existing):
        request.state.csrf_token = existing
        return
    request.state.csrf_token = create_csrf_token()
    request.state.set_csrf_cookie = True


def apply_pending_csrf_cookie(request: Request, response: Response) -> None:
    if getattr(request.state, "set_csrf_cookie", False):
        apply_csrf_cookie(response, request.state.csrf_token)


def csrf_form_token(request: Request) -> str:
    token = getattr(request.state, "csrf_token", None)
    if isinstance(token, str) and token:
        return token
    return request.cookies.get(CSRF_COOKIE_NAME, "")


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="strict",
    )


def _path_exempt(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


async def _submitted_token(request: Request) -> str:
    header = request.headers.get(CSRF_HEADER, "")
    if header:
        return header
    query = request.query_params.get(CSRF_FORM_FIELD, "")
    if query:
        return query
    content_type = request.headers.get("content-type", "")
    if (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        form = await request.form()
        field = form.get(CSRF_FORM_FIELD)
        if field is not None:
            return str(field)
    return ""


def csrf_required(request: Request) -> bool:
    """Return True when this request must include a valid CSRF token."""
    if request.method not in _UNSAFE_METHODS:
        return False
    if _path_exempt(request.url.path):
        return False
    return bool(request.cookies.get(settings.session_cookie_name))


async def verify_csrf(request: Request) -> bool:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    if not validate_csrf_token(cookie_token):
        return False
    submitted = await _submitted_token(request)
    if not submitted or not validate_csrf_token(submitted):
        return False
    return secrets_match(cookie_token, submitted)


async def reject_invalid_csrf(request: Request) -> bool:
    """Return True when the request should be rejected with 403."""
    if not csrf_required(request):
        return False
    if await verify_csrf(request):
        return False
    logger.warning("Rejected request with invalid or missing CSRF token: %s", request.url.path)
    return True
