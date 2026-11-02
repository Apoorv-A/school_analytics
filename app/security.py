"""Password hashing and signed session tokens.

Passwords use bcrypt. Session state is a signed, timestamped token rather than a
raw user id, so a tampered cookie is rejected before any lookup happens.
"""

from __future__ import annotations

import hmac
import logging

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

logger = logging.getLogger(__name__)

_SESSION_SALT = "school-analytics.session.v1"
_MFA_PENDING_SALT = "school-analytics.mfa-pending.v1"
_MFA_ENROL_SALT = "school-analytics.mfa-enrol.v1"

MFA_TOKEN_MAX_AGE = 300

# bcrypt truncates at 72 bytes; reject longer input rather than silently ignoring the tail.
MAX_PASSWORD_BYTES = 72


class PasswordTooLongError(ValueError):
    pass


def hash_password(plain_password: str) -> str:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except ValueError:
        # Malformed or legacy hash on the record; treat as a failed attempt.
        logger.warning("Rejected a login against a malformed password hash.")
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=_SESSION_SALT)


def create_session_token(
    user_id: int, role: str, tenant_id: str, session_version: int = 1
) -> str:
    return _serializer().dumps(
        {"uid": user_id, "role": role, "tid": tenant_id, "sv": session_version}
    )


def _mfa_serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=salt)


def create_mfa_pending_token(user_id: int, tenant_id: str) -> str:
    return _mfa_serializer(_MFA_PENDING_SALT).dumps({"uid": user_id, "tid": tenant_id})


def read_mfa_pending_token(token: str) -> dict[str, object] | None:
    return _read_mfa_token(token, _MFA_PENDING_SALT)


def create_mfa_enrol_token(user_id: int, tenant_id: str) -> str:
    return _mfa_serializer(_MFA_ENROL_SALT).dumps({"uid": user_id, "tid": tenant_id})


def read_mfa_enrol_token(token: str) -> dict[str, object] | None:
    return _read_mfa_token(token, _MFA_ENROL_SALT)


def _read_mfa_token(token: str, salt: str) -> dict[str, object] | None:
    if not token:
        return None
    try:
        payload = _mfa_serializer(salt).loads(token, max_age=MFA_TOKEN_MAX_AGE)
    except SignatureExpired:
        return None
    except BadSignature:
        logger.warning("Rejected an MFA token with an invalid signature.")
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("uid"), int):
        return None
    if not isinstance(payload.get("tid"), str):
        return None
    return payload


def read_session_token(
    token: str, max_age: int | None = None
) -> dict[str, object] | None:
    """Return the token payload, or None if it is missing, expired, or tampered with."""
    if not token:
        return None
    age = settings.session_max_age if max_age is None else max_age
    try:
        payload = _serializer().loads(token, max_age=age)
    except SignatureExpired:
        return None
    except BadSignature:
        logger.warning("Rejected a session cookie with an invalid signature.")
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("uid"), int):
        return None
    if not isinstance(payload.get("tid"), str):
        return None
    sv = payload.get("sv", 1)
    if not isinstance(sv, int) or sv < 1:
        return None
    return payload


def secrets_match(candidate: str, expected: str) -> bool:
    """Constant-time comparison for non-hashed secrets such as CSRF or API tokens."""
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
