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


def create_session_token(user_id: int, role: str) -> str:
    return _serializer().dumps({"uid": user_id, "role": role})


def read_session_token(token: str) -> dict[str, object] | None:
    """Return the token payload, or None if it is missing, expired, or tampered with."""
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=settings.session_max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        logger.warning("Rejected a session cookie with an invalid signature.")
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("uid"), int):
        return None
    return payload


def secrets_match(candidate: str, expected: str) -> bool:
    """Constant-time comparison for non-hashed secrets such as CSRF or API tokens."""
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
