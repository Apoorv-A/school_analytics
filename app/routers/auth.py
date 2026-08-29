"""Login and logout."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_optional_user
from app.models import Role, User
from app.portals import PORTAL_HOME
from app.schemas import LoginForm
from app.security import create_session_token, hash_password, verify_password
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_MAX_ATTEMPTS = 8
_LOCKOUT_SECONDS = 300
_attempts: dict[str, tuple[int, float]] = {}
_attempts_lock = threading.Lock()

# A dummy hash keeps the failure path's timing close to the success path so a
# missing account is not distinguishable from a wrong password by response time.
_DUMMY_HASH = hash_password("this-account-does-not-exist")


def _throttle_key(email: str, client_host: str) -> str:
    # Hashed so no email address is ever held in the throttle table or a log line.
    raw = f"{email.strip().lower()}|{client_host}".encode()
    return hashlib.sha256(raw).hexdigest()


def _is_locked_out(key: str) -> bool:
    with _attempts_lock:
        record = _attempts.get(key)
        if record is None:
            return False
        count, first_seen = record
        if time.monotonic() - first_seen > _LOCKOUT_SECONDS:
            _attempts.pop(key, None)
            return False
        return count >= _MAX_ATTEMPTS


def _record_failure(key: str) -> None:
    with _attempts_lock:
        count, first_seen = _attempts.get(key, (0, time.monotonic()))
        if time.monotonic() - first_seen > _LOCKOUT_SECONDS:
            count, first_seen = 0, time.monotonic()
        _attempts[key] = (count + 1, first_seen)


def _clear_failures(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


def _demo_accounts(db: Session) -> list[dict[str, str]]:
    """One seeded account per role, for local evaluation only.

    Returns nothing unless DEMO_MODE is explicitly enabled, so a real deployment never
    advertises valid email addresses on its sign-in page.
    """
    if not settings.demo_mode:
        return []
    accounts: list[dict[str, str]] = []
    for role in (Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT):
        user = db.scalars(
            select(User).where(User.role == role, User.is_active.is_(True)).limit(1)
        ).first()
        if user is not None:
            accounts.append({"role": role.label, "email": user.email})
    return accounts


def _set_session_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=create_session_token(user.id, user.role.value),
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Expire the session cookie.

    A browser only treats a deletion as being for the same cookie when the
    attributes match the ones it was set with, so a deletion that omits Secure is
    commonly ignored for a Secure cookie. The token is self-contained and signed
    with no server-side revocation, so a cookie surviving logout stays valid
    until it expires on its own: these attributes have to mirror
    `_set_session_cookie` exactly.
    """
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
    )


def _login_context(db: Session, error: str | None) -> dict[str, object]:
    return {
        "error": error,
        "demo_accounts": _demo_accounts(db),
        "demo_password": settings.demo_password if settings.demo_mode else "",
    }


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> Response:
    if user is not None:
        return RedirectResponse(
            PORTAL_HOME[user.role], status_code=status.HTTP_303_SEE_OTHER
        )
    return templates.TemplateResponse(request, "login.html", _login_context(db, None))


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    generic_error = "Email or password is incorrect."
    client_host = request.client.host if request.client else "unknown"

    try:
        credentials = LoginForm(email=email, password=password)
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(db, generic_error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    key = _throttle_key(credentials.email, client_host)
    if _is_locked_out(key):
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(db, "Too many failed attempts. Try again in a few minutes."),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    normalized_email = credentials.email.strip().lower()
    user = db.scalars(select(User).where(User.email == normalized_email)).first()
    password_hash = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(credentials.password, password_hash)

    if user is None or not password_ok or not user.is_active:
        _record_failure(key)
        logger.info("Failed login attempt from %s", client_host)
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(db, generic_error),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    _clear_failures(key)
    user.last_login_at = datetime.now(UTC)
    db.commit()

    response = RedirectResponse(
        PORTAL_HOME[user.role], status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(response, user)
    return response


@router.post("/logout")
@router.get("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response
