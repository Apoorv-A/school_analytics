"""Password reset, session invalidation, and forced password-change helpers."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import SecurityAuditAction, SecurityAuditTarget, record_security_event
from app.config import settings
from app.models import PasswordResetToken, User
from app.security import hash_password

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


class PasswordResetError(Exception):
    """Raised when a reset token is missing, expired, or already used."""


def _hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def invalidate_user_sessions(user: User) -> None:
    """Bump session version so existing signed cookies are rejected."""
    user.session_version = (user.session_version or 1) + 1


def revoke_password_reset_tokens(db: Session, user_id: int) -> None:
    """Mark all unused reset tokens for a user as consumed."""
    now = datetime.now(UTC)
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )


def set_user_password(
    db: Session,
    user: User,
    new_password: str,
    *,
    clear_must_change: bool = True,
    request: Request | None = None,
) -> None:
    """Set password, invalidate sessions, and revoke outstanding reset tokens."""
    user.password_hash = hash_password(new_password)
    if clear_must_change:
        user.must_change_password = False
    invalidate_user_sessions(user)
    record_security_event(
        db,
        tenant_id=user.tenant_id,
        action=SecurityAuditAction.SESSION_REVOKED,
        target_type=SecurityAuditTarget.USER,
        target_id=user.id,
        actor_user_id=user.id,
        metadata={"session_version": user.session_version},
        request=request,
    )
    revoke_password_reset_tokens(db, user.id)
    db.commit()


def issue_password_reset_token(
    db: Session,
    user: User,
    *,
    created_by_user_id: int | None = None,
    max_age_seconds: int | None = None,
) -> str:
    """Create a single-use reset token; returns the raw secret (show once)."""
    revoke_password_reset_tokens(db, user.id)
    raw = secrets.token_urlsafe(32)
    age = max_age_seconds or settings.password_reset_token_max_age
    record = PasswordResetToken(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=_hash_reset_token(raw),
        expires_at=datetime.now(UTC) + timedelta(seconds=age),
        created_by_user_id=created_by_user_id,
    )
    db.add(record)
    db.commit()
    return raw


def consume_password_reset_token(db: Session, raw_token: str) -> User:
    """Validate token and return the user; raises PasswordResetError on failure."""
    if not raw_token.strip():
        raise PasswordResetError("Invalid or expired reset link.")
    token_hash = _hash_reset_token(raw_token.strip())
    record = db.scalars(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).first()
    if record is None:
        raise PasswordResetError("Invalid or expired reset link.")
    now = datetime.now(UTC)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if record.used_at is not None or now >= expires_at:
        raise PasswordResetError("Invalid or expired reset link.")
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise PasswordResetError("Invalid or expired reset link.")
    record.used_at = now
    db.flush()
    return user


def verify_reset_token_for_display(db: Session, raw_token: str) -> User | None:
    """Non-consuming check for the reset form GET handler."""
    try:
        token_hash = _hash_reset_token(raw_token.strip())
    except Exception:
        return None
    record = db.scalars(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).first()
    if record is None or record.used_at is not None:
        return None
    now = datetime.now(UTC)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if now >= expires_at:
        return None
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    return user
