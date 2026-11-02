"""Login, logout, and TOTP MFA."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Path, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.account_lifecycle import (
    PasswordResetError,
    consume_password_reset_token,
    invalidate_user_sessions,
    issue_password_reset_token,
    set_user_password,
    verify_reset_token_for_display,
)
from app.audit import (
    SecurityAuditAction,
    SecurityAuditTarget,
    record_security_event,
)
from app.client_ip import client_host
from app.config import settings
from app.csrf import clear_csrf_cookie, set_csrf_cookie
from app.db import get_db
from app.deps import get_current_user, get_optional_user, require_roles
from app.mfa import (
    consume_recovery_code,
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    get_pending_totp_secret,
    get_user_totp_secret,
    mfa_required_for_user,
    provisioning_uri,
    replace_user_recovery_codes,
    reset_user_mfa,
    user_has_totp,
    verify_strong_authentication,
    verify_totp,
)
from app.models import Role, User
from app.portals import PORTAL_HOME
from app.schemas import (
    AdminInviteUserRequest,
    ChangePasswordForm,
    ForgotPasswordForm,
    LoginForm,
    MfaEnrolConfirmForm,
    MfaEnrolConfirmRequest,
    MfaEnrolStartRequest,
    MfaRecoveryCodesRequest,
    MfaRecoveryLoginForm,
    MfaRecoveryLoginRequest,
    MfaVerifyForm,
    MfaVerifyRequest,
    ResetPasswordForm,
)
from app.security import (
    MFA_TOKEN_MAX_AGE,
    create_mfa_enrol_token,
    create_mfa_pending_token,
    create_session_token,
    hash_password,
    read_mfa_enrol_token,
    read_mfa_pending_token,
    verify_password,
)
from app.templating import templates
from app.tenant.context import get_tenant_context, require_tenant_context
from app.throttle import LoginThrottleStoreUnavailable, get_login_throttle_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_FORGOT_ACK_MESSAGE = (
    "If an account exists for that email, password reset instructions have been sent."
)

# A dummy hash keeps the failure path's timing close to the success path so a
# missing account is not distinguishable from a wrong password by response time.
_DUMMY_HASH = hash_password("this-account-does-not-exist")


_THROTTLE_UNAVAILABLE_MESSAGE = (
    "Authentication is temporarily unavailable. Try again later."
)


def _account_throttle_key(email: str, tenant_id: str) -> str:
    raw = f"acct|{tenant_id}|{email.strip().lower()}".encode()
    return hashlib.sha256(raw).hexdigest()


def _ip_throttle_key(client_host: str, tenant_id: str) -> str:
    raw = f"ip|{tenant_id}|{client_host}".encode()
    return hashlib.sha256(raw).hexdigest()


def _throttle_key(email: str, client_host: str, tenant_id: str) -> str:
    # Combined key for forgot-password flows that must not reveal account existence.
    raw = f"{tenant_id}|{email.strip().lower()}|{client_host}".encode()
    return hashlib.sha256(raw).hexdigest()


def _mfa_throttle_key(user_id: int, client_host: str, tenant_id: str) -> str:
    raw = f"mfa|{tenant_id}|{user_id}|{client_host}".encode()
    return hashlib.sha256(raw).hexdigest()


def _throttle() -> object:
    return get_login_throttle_store()


def _login_throttle_keys(
    *,
    email: str,
    client_host: str,
    tenant_id: str,
) -> tuple[str, str, str]:
    tenant_text = str(tenant_id)
    return (
        _account_throttle_key(email, tenant_text),
        _ip_throttle_key(client_host, tenant_text),
        _throttle_key(email, client_host, tenant_text),
    )


def _is_locked_out(store: object, *keys: str) -> bool:
    return any(store.is_locked_out(key) for key in keys)


def _record_auth_failures(store: object, *keys: str) -> bool:
    """Record failures on all keys; return True if any key reached lockout."""
    locked = False
    for key in keys:
        if store.record_failure(key):
            locked = True
    return locked


def _clear_auth_failures(store: object, *keys: str) -> None:
    for key in keys:
        store.clear_failures(key)


def _record_login_lockout(
    db: Session,
    *,
    tenant_id,
    request: Request,
    scope: str,
) -> None:
    record_security_event(
        db,
        tenant_id=tenant_id,
        action=SecurityAuditAction.LOGIN_LOCKOUT,
        target_type=SecurityAuditTarget.USER,
        target_id="unknown",
        metadata={"scope": scope},
        request=request,
    )


def _record_throttle_unavailable(
    db: Session,
    *,
    tenant_id,
    request: Request,
) -> None:
    record_security_event(
        db,
        tenant_id=tenant_id,
        action=SecurityAuditAction.AUTH_THROTTLE_UNAVAILABLE,
        target_type=SecurityAuditTarget.USER,
        target_id="unknown",
        request=request,
    )


def _post_auth_redirect(user: User) -> str:
    if user.must_change_password:
        return "/change-password"
    return PORTAL_HOME[user.role]


def _demo_accounts(db: Session) -> list[dict[str, str]]:
    """One seeded account per role, for local evaluation only."""
    if not settings.demo_mode:
        return []
    tenant_ctx = require_tenant_context()
    accounts: list[dict[str, str]] = []
    for role in (Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT):
        user = db.scalars(
            select(User)
            .where(
                User.role == role,
                User.is_active.is_(True),
                User.tenant_id == tenant_ctx.tenant_id,
            )
            .limit(1)
        ).first()
        if user is not None:
            accounts.append({"role": role.label, "email": user.email})
    return accounts


def _set_session_cookie(response: Response, user: User) -> None:
    ctx = get_tenant_context()
    max_age = ctx.settings.session_max_age if ctx else settings.session_max_age
    response.set_cookie(
        key=settings.session_cookie_name,
        value=create_session_token(
            user.id,
            user.role.value,
            str(user.tenant_id),
            user.session_version,
        ),
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    set_csrf_cookie(response)


def _set_mfa_pending_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.mfa_pending_cookie_name,
        value=token,
        max_age=MFA_TOKEN_MAX_AGE,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )


def _set_mfa_enrol_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.mfa_enrol_cookie_name,
        value=token,
        max_age=MFA_TOKEN_MAX_AGE,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_mfa_cookies(response: Response) -> None:
    for name in (settings.mfa_pending_cookie_name, settings.mfa_enrol_cookie_name):
        response.delete_cookie(
            key=name,
            path="/",
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="strict",
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
    _clear_mfa_cookies(response)
    clear_csrf_cookie(response)


def _login_context(
    db: Session,
    *,
    error: str | None = None,
    mfa_step: bool = False,
    recovery_step: bool = False,
    enrolment_step: bool = False,
    provisioning_uri: str | None = None,
    manual_secret: str | None = None,
) -> dict[str, object]:
    return {
        "error": error,
        "demo_accounts": _demo_accounts(db),
        "demo_password": settings.demo_password if settings.demo_mode else "",
        "mfa_step": mfa_step,
        "recovery_step": recovery_step,
        "enrolment_step": enrolment_step,
        "provisioning_uri": provisioning_uri,
        "manual_secret": manual_secret,
    }


def _authenticate_user(
    request: Request,
    credentials: LoginForm,
    db: Session,
) -> tuple[User | None, str | None, int]:
    """Validate credentials. Returns (user, error_message, http_status) on failure."""
    generic_error = "Email or password is incorrect."
    remote_host = client_host(request)
    tenant_ctx = require_tenant_context()
    store = _throttle()
    account_key, ip_key, _combined_key = _login_throttle_keys(
        email=credentials.email,
        client_host=remote_host,
        tenant_id=str(tenant_ctx.tenant_id),
    )
    throttle_keys = (account_key, ip_key)
    try:
        if _is_locked_out(store, *throttle_keys):
            record_security_event(
                db,
                tenant_id=tenant_ctx.tenant_id,
                action=SecurityAuditAction.LOGIN_FAILURE,
                target_type=SecurityAuditTarget.USER,
                target_id="unknown",
                metadata={"reason": "throttled"},
                request=request,
            )
            db.commit()
            return (
                None,
                "Too many failed attempts. Try again in a few minutes.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except LoginThrottleStoreUnavailable:
        _record_throttle_unavailable(
            db, tenant_id=tenant_ctx.tenant_id, request=request
        )
        db.commit()
        return None, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE

    normalized_email = credentials.email.strip().lower()
    user = db.scalars(
        select(User).where(
            User.email == normalized_email,
            User.tenant_id == tenant_ctx.tenant_id,
        )
    ).first()
    password_hash = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(credentials.password, password_hash)

    if user is None or not password_ok or not user.is_active:
        try:
            if _record_auth_failures(store, *throttle_keys):
                _record_login_lockout(
                    db,
                    tenant_id=tenant_ctx.tenant_id,
                    request=request,
                    scope="login",
                )
        except LoginThrottleStoreUnavailable:
            _record_throttle_unavailable(
                db, tenant_id=tenant_ctx.tenant_id, request=request
            )
            db.commit()
            return (
                None,
                _THROTTLE_UNAVAILABLE_MESSAGE,
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        logger.info("Failed login attempt from %s", remote_host)
        reason = "inactive" if user is not None and not user.is_active else "invalid_credentials"
        record_security_event(
            db,
            tenant_id=tenant_ctx.tenant_id,
            action=SecurityAuditAction.LOGIN_FAILURE,
            target_type=SecurityAuditTarget.USER,
            target_id=user.id if user is not None else "unknown",
            actor_user_id=user.id if user is not None else None,
            metadata={"reason": reason},
            request=request,
        )
        db.commit()
        return None, generic_error, status.HTTP_401_UNAUTHORIZED

    try:
        _clear_auth_failures(store, *throttle_keys)
    except LoginThrottleStoreUnavailable:
        _record_throttle_unavailable(
            db, tenant_id=tenant_ctx.tenant_id, request=request
        )
        db.commit()
        return None, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE
    return user, None, status.HTTP_200_OK


def _complete_login(user: User, db: Session, *, request: Request | None = None) -> None:
    user.last_login_at = datetime.now(UTC)
    record_security_event(
        db,
        tenant_id=user.tenant_id,
        action=SecurityAuditAction.LOGIN_SUCCESS,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        request=request,
    )
    db.commit()


def _load_user_from_mfa_token(
    db: Session,
    token: str,
    *,
    enrol: bool,
) -> User | None:
    tenant_ctx = require_tenant_context()
    payload = read_mfa_enrol_token(token) if enrol else read_mfa_pending_token(token)
    if payload is None:
        return None
    if payload.get("tid") != str(tenant_ctx.tenant_id):
        return None
    user = db.get(User, payload["uid"])
    if user is None or not user.is_active or user.tenant_id != tenant_ctx.tenant_id:
        return None
    return user


def _login_mfa_state(user: User) -> tuple[bool, bool]:
    tenant_ctx = require_tenant_context()
    if not mfa_required_for_user(user, tenant_ctx.settings):
        return False, False
    if user_has_totp(user):
        return True, False
    return False, True


def _api_login_body(
    user: User,
    *,
    mfa_pending: bool,
    mfa_enrolment_needed: bool,
    mfa_token: str | None = None,
    enrolment_token: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "userId": user.id,
        "role": user.role.value,
        "mfaPending": mfa_pending,
        "mfaEnrolmentNeeded": mfa_enrolment_needed,
    }
    if mfa_token is not None:
        body["mfaToken"] = mfa_token
    if enrolment_token is not None:
        body["enrolmentToken"] = enrolment_token
    return body


def _verify_mfa_code(
    request: Request,
    user: User,
    code: str,
    db: Session,
) -> tuple[bool, str | None, int]:
    remote_host = client_host(request)
    tenant_ctx = require_tenant_context()
    store = _throttle()
    key = _mfa_throttle_key(user.id, remote_host, str(tenant_ctx.tenant_id))
    try:
        if store.is_locked_out(key):
            record_security_event(
                db,
                tenant_id=tenant_ctx.tenant_id,
                action=SecurityAuditAction.MFA_VERIFY_FAILURE,
                target_type=SecurityAuditTarget.USER,
                target_id=user.id,
                actor_user_id=user.id,
                metadata={"reason": "throttled"},
                request=request,
            )
            db.commit()
            return (
                False,
                "Too many failed attempts. Try again in a few minutes.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except LoginThrottleStoreUnavailable:
        _record_throttle_unavailable(
            db, tenant_id=tenant_ctx.tenant_id, request=request
        )
        db.commit()
        return False, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE

    secret = get_user_totp_secret(user)
    if secret is None or not verify_totp(secret, code):
        try:
            if store.record_failure(key):
                _record_login_lockout(
                    db,
                    tenant_id=tenant_ctx.tenant_id,
                    request=request,
                    scope="mfa",
                )
        except LoginThrottleStoreUnavailable:
            _record_throttle_unavailable(
                db, tenant_id=tenant_ctx.tenant_id, request=request
            )
            db.commit()
            return False, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE
        logger.info("Failed MFA verification for user %s from %s", user.id, remote_host)
        record_security_event(
            db,
            tenant_id=tenant_ctx.tenant_id,
            action=SecurityAuditAction.MFA_VERIFY_FAILURE,
            target_type=SecurityAuditTarget.USER,
            target_id=user.id,
            actor_user_id=user.id,
            metadata={"reason": "invalid_code"},
            request=request,
        )
        db.commit()
        return False, "Authentication code is incorrect.", status.HTTP_401_UNAUTHORIZED

    try:
        store.clear_failures(key)
    except LoginThrottleStoreUnavailable:
        _record_throttle_unavailable(
            db, tenant_id=tenant_ctx.tenant_id, request=request
        )
        db.commit()
        return False, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE

    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.MFA_VERIFY_SUCCESS,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        request=request,
    )
    _complete_login(user, db, request=request)
    return True, None, status.HTTP_200_OK


def _verify_recovery_code_login(
    request: Request,
    user: User,
    raw_code: str,
    db: Session,
) -> tuple[bool, str | None, int]:
    remote_host = client_host(request)
    tenant_ctx = require_tenant_context()
    store = _throttle()
    key = _mfa_throttle_key(user.id, remote_host, str(tenant_ctx.tenant_id))
    try:
        if store.is_locked_out(key):
            record_security_event(
                db,
                tenant_id=tenant_ctx.tenant_id,
                action=SecurityAuditAction.MFA_VERIFY_FAILURE,
                target_type=SecurityAuditTarget.USER,
                target_id=user.id,
                actor_user_id=user.id,
                metadata={"reason": "throttled"},
                request=request,
            )
            db.commit()
            return (
                False,
                "Too many failed attempts. Try again in a few minutes.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except LoginThrottleStoreUnavailable:
        _record_throttle_unavailable(
            db, tenant_id=tenant_ctx.tenant_id, request=request
        )
        db.commit()
        return False, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE

    if not consume_recovery_code(db, user, raw_code):
        try:
            if store.record_failure(key):
                _record_login_lockout(
                    db,
                    tenant_id=tenant_ctx.tenant_id,
                    request=request,
                    scope="mfa",
                )
        except LoginThrottleStoreUnavailable:
            _record_throttle_unavailable(
                db, tenant_id=tenant_ctx.tenant_id, request=request
            )
            db.commit()
            return False, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE
        logger.info(
            "Failed MFA recovery verification for user %s from %s", user.id, remote_host
        )
        record_security_event(
            db,
            tenant_id=tenant_ctx.tenant_id,
            action=SecurityAuditAction.MFA_VERIFY_FAILURE,
            target_type=SecurityAuditTarget.USER,
            target_id=user.id,
            actor_user_id=user.id,
            metadata={"reason": "invalid_recovery"},
            request=request,
        )
        db.commit()
        return False, "Recovery code is incorrect or already used.", status.HTTP_401_UNAUTHORIZED

    try:
        store.clear_failures(key)
    except LoginThrottleStoreUnavailable:
        _record_throttle_unavailable(
            db, tenant_id=tenant_ctx.tenant_id, request=request
        )
        db.commit()
        return False, _THROTTLE_UNAVAILABLE_MESSAGE, status.HTTP_503_SERVICE_UNAVAILABLE

    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.MFA_RECOVERY_CODE_USED,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        request=request,
    )
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.MFA_VERIFY_SUCCESS,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        request=request,
    )
    _complete_login(user, db, request=request)
    return True, None, status.HTTP_200_OK


def _complete_mfa_login_response(user: User) -> RedirectResponse:
    response = RedirectResponse(
        _post_auth_redirect(user), status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(response, user)
    _clear_mfa_cookies(response)
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    switch: bool = Query(default=False),
    recovery: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> Response:
    if user is not None and not switch:
        return RedirectResponse(
            PORTAL_HOME[user.role], status_code=status.HTTP_303_SEE_OTHER
        )
    context = _login_context(db)
    if request.cookies.get(settings.mfa_pending_cookie_name):
        if recovery:
            context = _login_context(db, recovery_step=True)
        else:
            context = _login_context(db, mfa_step=True)
    response = templates.TemplateResponse(request, "login.html", context)
    if user is not None and switch:
        clear_session_cookie(response)
        _clear_mfa_cookies(response)
    return response


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(default=""),
    password: str = Form(default=""),
    mfa_code: str = Form(default=""),
    recovery_code: str = Form(default=""),
    enrol_confirm_code: str = Form(default=""),
    db: Session = Depends(get_db),
) -> Response:
    generic_error = "Email or password is incorrect."

    if recovery_code.strip():
        token = request.cookies.get(settings.mfa_pending_cookie_name, "")
        user = _load_user_from_mfa_token(db, token, enrol=False)
        if user is None:
            response = templates.TemplateResponse(
                request,
                "login.html",
                _login_context(
                    db,
                    error="Your verification session expired. Sign in again.",
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_mfa_cookies(response)
            return response
        try:
            payload = MfaRecoveryLoginForm(recoveryCode=recovery_code.strip())
        except ValidationError:
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(
                    db,
                    error="Enter a valid recovery code.",
                    recovery_step=True,
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        ok, error, code = _verify_recovery_code_login(
            request, user, payload.recoveryCode, db
        )
        if not ok:
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(db, error=error, recovery_step=True),
                status_code=code,
            )
        return _complete_mfa_login_response(user)

    if mfa_code.strip():
        token = request.cookies.get(settings.mfa_pending_cookie_name, "")
        user = _load_user_from_mfa_token(db, token, enrol=False)
        if user is None:
            response = templates.TemplateResponse(
                request,
                "login.html",
                _login_context(
                    db,
                    error="Your verification session expired. Sign in again.",
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_mfa_cookies(response)
            return response
        try:
            payload = MfaVerifyForm(code=mfa_code.strip())
        except ValidationError:
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(
                    db,
                    error="Enter the 6-digit code from your authenticator app.",
                    mfa_step=True,
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        ok, error, code = _verify_mfa_code(request, user, payload.code, db)
        if not ok:
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(db, error=error, mfa_step=True),
                status_code=code,
            )
        return _complete_mfa_login_response(user)

    if enrol_confirm_code.strip():
        token = request.cookies.get(settings.mfa_enrol_cookie_name, "")
        user = _load_user_from_mfa_token(db, token, enrol=True)
        if user is None:
            response = templates.TemplateResponse(
                request,
                "login.html",
                _login_context(
                    db,
                    error="Your enrolment session expired. Sign in again.",
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_mfa_cookies(response)
            return response
        try:
            payload = MfaEnrolConfirmForm(code=enrol_confirm_code.strip())
        except ValidationError:
            pending = get_pending_totp_secret(user)
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(
                    db,
                    error="Enter the 6-digit code from your authenticator app.",
                    enrolment_step=True,
                    provisioning_uri=provisioning_uri(pending, user.email)
                    if pending
                    else None,
                    manual_secret=pending,
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        ok, error_msg = _confirm_mfa_enrolment(request, user, payload.code, db)
        if not ok:
            pending = get_pending_totp_secret(user)
            return templates.TemplateResponse(
                request,
                "login.html",
                _login_context(
                    db,
                    error=error_msg,
                    enrolment_step=True,
                    provisioning_uri=provisioning_uri(pending, user.email)
                    if pending
                    else None,
                    manual_secret=pending,
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return _complete_mfa_login_response(user)

    try:
        credentials = LoginForm(email=email, password=password)
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(db, error=generic_error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user, error, code = _authenticate_user(request, credentials, db)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(db, error=error),
            status_code=code,
        )

    mfa_pending, enrolment_needed = _login_mfa_state(user)
    tenant_ctx = require_tenant_context()

    if not mfa_pending and not enrolment_needed:
        _complete_login(user, db, request=request)
        response = RedirectResponse(
            _post_auth_redirect(user), status_code=status.HTTP_303_SEE_OTHER
        )
        _set_session_cookie(response, user)
        return response

    if enrolment_needed:
        secret = _start_mfa_enrolment(user, db)
        token = create_mfa_enrol_token(user.id, str(tenant_ctx.tenant_id))
        response = templates.TemplateResponse(
            request,
            "login.html",
            _login_context(
                db,
                enrolment_step=True,
                provisioning_uri=provisioning_uri(secret, user.email),
                manual_secret=secret,
            ),
            status_code=status.HTTP_200_OK,
        )
        _set_mfa_enrol_cookie(response, token)
        return response

    token = create_mfa_pending_token(user.id, str(tenant_ctx.tenant_id))
    response = templates.TemplateResponse(
        request,
        "login.html",
        _login_context(db, mfa_step=True),
        status_code=status.HTTP_200_OK,
    )
    _set_mfa_pending_cookie(response, token)
    return response


@router.post("/api/auth/login", include_in_schema=False)
def api_login(
    request: Request,
    credentials: LoginForm,
    db: Session = Depends(get_db),
) -> Response:
    """JSON login for config-repo QA regression.

    Form login at ``POST /login`` is unchanged.
    """
    user, error, code = _authenticate_user(request, credentials, db)
    if user is None:
        return JSONResponse({"error": error}, status_code=code)

    mfa_pending, enrolment_needed = _login_mfa_state(user)
    tenant_ctx = require_tenant_context()

    if not mfa_pending and not enrolment_needed:
        _complete_login(user, db, request=request)
        response = JSONResponse(
            _api_login_body(user, mfa_pending=False, mfa_enrolment_needed=False)
        )
        _set_session_cookie(response, user)
        return response

    if enrolment_needed:
        token = create_mfa_enrol_token(user.id, str(tenant_ctx.tenant_id))
        return JSONResponse(
            _api_login_body(
                user,
                mfa_pending=False,
                mfa_enrolment_needed=True,
                enrolment_token=token,
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    token = create_mfa_pending_token(user.id, str(tenant_ctx.tenant_id))
    return JSONResponse(
        _api_login_body(
            user,
            mfa_pending=True,
            mfa_enrolment_needed=False,
            mfa_token=token,
        ),
        status_code=status.HTTP_200_OK,
    )


def _start_mfa_enrolment(user: User, db: Session) -> str:
    secret = generate_totp_secret()
    user.totp_pending_secret_enc = encrypt_secret(secret)
    db.commit()
    return secret


def _confirm_mfa_enrolment(
    request: Request,
    user: User,
    code: str,
    db: Session,
) -> tuple[bool, str | None]:
    remote_host = client_host(request)
    tenant_ctx = require_tenant_context()
    store = _throttle()
    key = _mfa_throttle_key(user.id, remote_host, str(tenant_ctx.tenant_id))
    try:
        if store.is_locked_out(key):
            return False, "Too many failed attempts. Try again in a few minutes."
    except LoginThrottleStoreUnavailable:
        return False, _THROTTLE_UNAVAILABLE_MESSAGE

    pending = get_pending_totp_secret(user)
    if pending is None or not verify_totp(pending, code):
        try:
            store.record_failure(key)
        except LoginThrottleStoreUnavailable:
            return False, _THROTTLE_UNAVAILABLE_MESSAGE
        logger.info("Failed MFA enrolment for user %s from %s", user.id, remote_host)
        return False, "Authentication code is incorrect."

    user.totp_secret_enc = encrypt_secret(pending)
    user.totp_pending_secret_enc = None
    user.mfa_enrolled_at = datetime.now(UTC)
    try:
        store.clear_failures(key)
    except LoginThrottleStoreUnavailable:
        return False, _THROTTLE_UNAVAILABLE_MESSAGE
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.MFA_ENROLLED,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        request=request,
    )
    _complete_login(user, db, request=request)
    return True, None


@router.post("/api/auth/mfa/verify", include_in_schema=False)
def api_mfa_verify(
    request: Request,
    payload: MfaVerifyRequest,
    db: Session = Depends(get_db),
) -> Response:
    token = payload.mfaToken or request.cookies.get(settings.mfa_pending_cookie_name, "")

    user = _load_user_from_mfa_token(db, token, enrol=False)
    if user is None:
        return JSONResponse(
            {"error": "Verification session expired. Sign in again."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    ok, error, code = _verify_mfa_code(request, user, payload.code, db)
    if not ok:
        return JSONResponse({"error": error}, status_code=code)

    response = JSONResponse(
        _api_login_body(user, mfa_pending=False, mfa_enrolment_needed=False)
    )
    _set_session_cookie(response, user)
    _clear_mfa_cookies(response)
    return response


@router.post("/api/auth/mfa/enroll/start", include_in_schema=False)
def api_mfa_enroll_start(
    request: Request,
    body: MfaEnrolStartRequest | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> Response:
    target = user
    if target is None:
        token = request.cookies.get(settings.mfa_enrol_cookie_name, "")
        if not token and body is not None and body.enrolmentToken:
            token = body.enrolmentToken
        target = _load_user_from_mfa_token(db, token, enrol=True)

    if target is None:
        return JSONResponse(
            {"error": "Authentication required."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if user is not None and user.id != target.id and user.role is not Role.ADMIN:
        return JSONResponse(
            {"error": "Not allowed."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    secret = _start_mfa_enrolment(target, db)
    return JSONResponse(
        {
            "provisioningUri": provisioning_uri(secret, target.email),
            "secret": secret,
        }
    )


@router.post("/api/auth/mfa/enroll/confirm", include_in_schema=False)
def api_mfa_enroll_confirm(
    request: Request,
    payload: MfaEnrolConfirmRequest,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> Response:
    tenant_ctx = require_tenant_context()
    target = user
    if target is None:
        token = payload.enrolmentToken or request.cookies.get(
            settings.mfa_enrol_cookie_name, ""
        )
        target = _load_user_from_mfa_token(db, token, enrol=True)

    if target is None:
        return JSONResponse(
            {"error": "Authentication required."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if user is not None and user.id != target.id and user.role is not Role.ADMIN:
        return JSONResponse(
            {"error": "Not allowed."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    ok, error = _confirm_mfa_enrolment(request, target, payload.code, db)
    if not ok:
        return JSONResponse({"error": error}, status_code=status.HTTP_401_UNAUTHORIZED)

    response = JSONResponse(
        _api_login_body(target, mfa_pending=False, mfa_enrolment_needed=False)
    )
    if mfa_required_for_user(target, tenant_ctx.settings):
        _set_session_cookie(response, target)
    _clear_mfa_cookies(response)
    return response


@router.post("/login/mfa/recovery")
def login_mfa_recovery_submit(
    request: Request,
    recovery_code: str = Form(default=""),
    db: Session = Depends(get_db),
) -> Response:
    token = request.cookies.get(settings.mfa_pending_cookie_name, "")
    user = _load_user_from_mfa_token(db, token, enrol=False)
    if user is None:
        response = templates.TemplateResponse(
            request,
            "login.html",
            _login_context(
                db,
                error="Your verification session expired. Sign in again.",
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        _clear_mfa_cookies(response)
        return response
    try:
        payload = MfaRecoveryLoginForm(recoveryCode=recovery_code.strip())
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(
                db,
                error="Enter a valid recovery code.",
                recovery_step=True,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ok, error, code = _verify_recovery_code_login(
        request, user, payload.recoveryCode, db
    )
    if not ok:
        return templates.TemplateResponse(
            request,
            "login.html",
            _login_context(db, error=error, recovery_step=True),
            status_code=code,
        )
    return _complete_mfa_login_response(user)


@router.post("/api/auth/mfa/recovery", include_in_schema=False)
def api_mfa_recovery(
    request: Request,
    payload: MfaRecoveryLoginRequest,
    db: Session = Depends(get_db),
) -> Response:
    token = payload.mfaToken or request.cookies.get(settings.mfa_pending_cookie_name, "")

    user = _load_user_from_mfa_token(db, token, enrol=False)
    if user is None:
        return JSONResponse(
            {"error": "Verification session expired. Sign in again."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    ok, error, code = _verify_recovery_code_login(
        request, user, payload.recoveryCode, db
    )
    if not ok:
        return JSONResponse({"error": error}, status_code=code)

    response = JSONResponse(
        _api_login_body(user, mfa_pending=False, mfa_enrolment_needed=False)
    )
    _set_session_cookie(response, user)
    _clear_mfa_cookies(response)
    return response


@router.post("/api/auth/mfa/recovery-codes", include_in_schema=False)
def api_mfa_recovery_codes(
    request: Request,
    payload: MfaRecoveryCodesRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    tenant_ctx = require_tenant_context()
    if not user_has_totp(user):
        return JSONResponse(
            {"error": "Enroll MFA before generating recovery codes."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not verify_strong_authentication(
        user, password=payload.password, totp_code=payload.totpCode
    ):
        return JSONResponse(
            {"error": "Re-authentication failed."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    codes = generate_recovery_codes()
    replace_user_recovery_codes(db, user, codes)
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.MFA_RECOVERY_CODES_GENERATED,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        metadata={"count": len(codes)},
        request=request,
    )
    db.commit()
    return JSONResponse({"recoveryCodes": codes})


@router.post("/api/admin/users/{user_id}/mfa/reset", include_in_schema=False)
def admin_mfa_reset(
    request: Request,
    user_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(Role.ADMIN)),
) -> Response:
    tenant_ctx = require_tenant_context()
    target = db.get(User, user_id)
    if target is None or target.tenant_id != tenant_ctx.tenant_id or not target.is_active:
        return JSONResponse({"detail": "User not found."}, status_code=404)

    reset_user_mfa(db, target)
    invalidate_user_sessions(target)
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.MFA_RESET,
        target_type=SecurityAuditTarget.USER,
        target_id=target.id,
        actor_user_id=admin.id,
        request=request,
    )
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.SESSION_REVOKED,
        target_type=SecurityAuditTarget.USER,
        target_id=target.id,
        actor_user_id=admin.id,
        metadata={"session_version": target.session_version},
        request=request,
    )
    db.commit()
    return JSONResponse(
        {
            "userId": target.id,
            "email": target.email,
            "mfaReset": True,
            "sessionVersion": target.session_version,
        }
    )


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> Response:
    if user is not None:
        return RedirectResponse(
            _post_auth_redirect(user), status_code=status.HTTP_303_SEE_OTHER
        )
    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        {"message": None, "error": None},
    )


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    email: str = Form(default=""),
    db: Session = Depends(get_db),
) -> Response:
    remote_host = client_host(request)
    tenant_ctx = require_tenant_context()
    store = _throttle()
    key = _throttle_key(email, remote_host, str(tenant_ctx.tenant_id))
    try:
        if store.is_locked_out(key):
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {
                    "error": "Too many requests. Try again in a few minutes.",
                    "message": None,
                },
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except LoginThrottleStoreUnavailable:
        _record_throttle_unavailable(
            db, tenant_id=tenant_ctx.tenant_id, request=request
        )
        db.commit()
        return templates.TemplateResponse(
            request,
            "forgot_password.html",
            {
                "error": _THROTTLE_UNAVAILABLE_MESSAGE,
                "message": None,
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        payload = ForgotPasswordForm(email=email)
    except ValidationError:
        try:
            store.record_failure(key)
        except LoginThrottleStoreUnavailable:
            _record_throttle_unavailable(
                db, tenant_id=tenant_ctx.tenant_id, request=request
            )
            db.commit()
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {
                    "error": _THROTTLE_UNAVAILABLE_MESSAGE,
                    "message": None,
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return templates.TemplateResponse(
            request,
            "forgot_password.html",
            {"message": _FORGOT_ACK_MESSAGE, "error": None},
            status_code=status.HTTP_200_OK,
        )

    normalized_email = payload.email.strip().lower()
    user = db.scalars(
        select(User).where(
            User.email == normalized_email,
            User.tenant_id == tenant_ctx.tenant_id,
            User.is_active.is_(True),
        )
    ).first()
    if user is not None:
        issue_password_reset_token(db, user)
        record_security_event(
            db,
            tenant_id=tenant_ctx.tenant_id,
            action=SecurityAuditAction.RESET_REQUESTED,
            target_type=SecurityAuditTarget.USER,
            target_id=user.id,
            actor_user_id=user.id,
            request=request,
        )
        db.commit()
        logger.info("Password reset requested for user %s from %s", user.id, remote_host)
    else:
        try:
            store.record_failure(key)
        except LoginThrottleStoreUnavailable:
            _record_throttle_unavailable(
                db, tenant_id=tenant_ctx.tenant_id, request=request
            )
            db.commit()
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {
                    "error": _THROTTLE_UNAVAILABLE_MESSAGE,
                    "message": None,
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        {"message": _FORGOT_ACK_MESSAGE, "error": None},
        status_code=status.HTTP_200_OK,
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
) -> Response:
    user = verify_reset_token_for_display(db, token) if token else None
    return templates.TemplateResponse(
        request,
        "reset_password.html",
        {
            "token": token,
            "valid": user is not None,
            "error": None,
            "message": None,
        },
        status_code=status.HTTP_200_OK if user is not None else status.HTTP_400_BAD_REQUEST,
    )


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    token: str = Form(default=""),
    password: str = Form(default=""),
    confirm_password: str = Form(default=""),
    db: Session = Depends(get_db),
) -> Response:
    try:
        payload = ResetPasswordForm(
            token=token, password=password, confirm_password=confirm_password
        )
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {
                "token": token,
                "valid": True,
                "error": "Check the password requirements and try again.",
                "message": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = consume_password_reset_token(db, payload.token)
    except PasswordResetError as exc:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {
                "token": "",
                "valid": False,
                "error": str(exc),
                "message": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    set_user_password(db, user, payload.password, request=request)
    record_security_event(
        db,
        tenant_id=user.tenant_id,
        action=SecurityAuditAction.RESET_COMPLETED,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        request=request,
    )
    db.commit()
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    user: User = Depends(get_current_user),
) -> Response:
    return templates.TemplateResponse(
        request,
        "change_password.html",
        {"forced": user.must_change_password, "error": None, "message": None},
    )


@router.post("/change-password")
def change_password_submit(
    request: Request,
    current_password: str = Form(default=""),
    password: str = Form(default=""),
    confirm_password: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    forced = user.must_change_password
    try:
        payload = ChangePasswordForm(
            current_password=current_password,
            password=password,
            confirm_password=confirm_password,
        )
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {
                "forced": forced,
                "error": "Check the password requirements and try again.",
                "message": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not verify_password(payload.current_password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {
                "forced": forced,
                "error": "Current password is incorrect.",
                "message": None,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    set_user_password(db, user, payload.password, request=request)
    db.refresh(user)
    response = RedirectResponse(
        PORTAL_HOME[user.role], status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(response, user)
    return response


@router.post("/api/admin/users/invite", include_in_schema=False)
def admin_invite_user(
    request: Request,
    payload: AdminInviteUserRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(Role.ADMIN)),
) -> Response:
    tenant_ctx = require_tenant_context()
    email = str(payload.email).lower()
    existing = db.scalars(
        select(User).where(User.tenant_id == tenant_ctx.tenant_id, User.email == email)
    ).first()
    if existing is not None:
        return JSONResponse(
            {"detail": "A user with this email already exists."},
            status_code=status.HTTP_409_CONFLICT,
        )

    temp_password = secrets.token_urlsafe(16)
    user = User(
        tenant_id=tenant_ctx.tenant_id,
        email=email,
        password_hash=hash_password(temp_password),
        full_name=payload.full_name,
        role=payload.role,
        must_change_password=True,
        is_active=True,
    )
    db.add(user)
    db.flush()

    activation_token = issue_password_reset_token(
        db, user, created_by_user_id=admin.id
    )
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.USER_INVITED,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=admin.id,
        metadata={"role": payload.role.value},
        request=request,
    )
    db.commit()
    return JSONResponse(
        {
            "userId": user.id,
            "email": user.email,
            "role": user.role.value,
            "tempPassword": temp_password,
            "activationToken": activation_token,
            "activationPath": f"/reset-password?token={activation_token}",
            "mustChangePassword": True,
        }
    )


@router.post("/api/admin/users/{user_id}/password-reset", include_in_schema=False)
def admin_password_reset(
    request: Request,
    user_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(Role.ADMIN)),
) -> Response:
    tenant_ctx = require_tenant_context()
    target = db.get(User, user_id)
    if target is None or target.tenant_id != tenant_ctx.tenant_id or not target.is_active:
        return JSONResponse({"detail": "User not found."}, status_code=404)

    raw = issue_password_reset_token(db, target, created_by_user_id=admin.id)
    record_security_event(
        db,
        tenant_id=tenant_ctx.tenant_id,
        action=SecurityAuditAction.RESET_ISSUED,
        target_type=SecurityAuditTarget.USER,
        target_id=target.id,
        actor_user_id=admin.id,
        request=request,
    )
    db.commit()
    return JSONResponse(
        {
            "userId": target.id,
            "email": target.email,
            "resetToken": raw,
            "resetPath": f"/reset-password?token={raw}",
        }
    )


@router.get("/logout", response_class=HTMLResponse)
def logout_page(request: Request) -> Response:
    """Support direct navigation/bookmarks while keeping logout POST-only."""
    if not request.cookies.get(settings.session_cookie_name):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "logout.html", {})


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> Response:
    if user is not None:
        record_security_event(
            db,
            tenant_id=user.tenant_id,
            action=SecurityAuditAction.LOGOUT,
            target_type=SecurityAuditTarget.SESSION,
            target_id=user.id,
            actor_user_id=user.id,
            request=request,
        )
        db.commit()
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response
