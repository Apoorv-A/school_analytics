"""TOTP MFA helpers: secret encryption, provisioning, recovery codes, and policy checks."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import UTC, datetime

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, UserMfaRecoveryCode
from app.security import verify_password
from app.tenant.settings import TenantSettings

_MFA_ISSUER = "School Analytics"
_TOTP_CODE_RE = re.compile(r"^\d{6}$")
_RECOVERY_CODE_RE = re.compile(r"^[0-9A-F]{4}-?[0-9A-F]{4}$", re.IGNORECASE)
_VALID_MFA_ROLES = frozenset({"admin", "teacher", "parent", "student"})
_RECOVERY_CODE_COUNT = 10


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_MFA_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    if not _TOTP_CODE_RE.match(code):
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def normalize_recovery_code(raw: str) -> str:
    cleaned = re.sub(r"[\s-]", "", raw.strip()).upper()
    if not _RECOVERY_CODE_RE.match(raw.strip()) and not re.fullmatch(
        r"[0-9A-F]{8}", cleaned
    ):
        raise ValueError("invalid recovery code format")
    return cleaned


def hash_recovery_code(raw: str) -> str:
    return hashlib.sha256(normalize_recovery_code(raw).encode("utf-8")).hexdigest()


def generate_recovery_codes(count: int = _RECOVERY_CODE_COUNT) -> list[str]:
    codes: set[str] = set()
    while len(codes) < count:
        raw = secrets.token_hex(4).upper()
        codes.add(f"{raw[:4]}-{raw[4:]}")
    return sorted(codes)


def replace_user_recovery_codes(db: Session, user: User, codes: list[str]) -> None:
    db.execute(
        delete(UserMfaRecoveryCode).where(UserMfaRecoveryCode.user_id == user.id)
    )
    for code in codes:
        db.add(
            UserMfaRecoveryCode(
                tenant_id=user.tenant_id,
                user_id=user.id,
                code_hash=hash_recovery_code(code),
            )
        )
    db.flush()


def consume_recovery_code(db: Session, user: User, raw_code: str) -> bool:
    try:
        code_hash = hash_recovery_code(raw_code)
    except ValueError:
        return False
    record = db.scalars(
        select(UserMfaRecoveryCode).where(
            UserMfaRecoveryCode.user_id == user.id,
            UserMfaRecoveryCode.tenant_id == user.tenant_id,
            UserMfaRecoveryCode.code_hash == code_hash,
            UserMfaRecoveryCode.used_at.is_(None),
        )
    ).first()
    if record is None:
        return False
    record.used_at = datetime.now(UTC)
    db.flush()
    return True


def clear_user_recovery_codes(db: Session, user_id: int) -> None:
    db.execute(delete(UserMfaRecoveryCode).where(UserMfaRecoveryCode.user_id == user_id))


def reset_user_mfa(db: Session, user: User) -> None:
    """Remove TOTP enrolment and all recovery codes for one user."""
    user.totp_secret_enc = None
    user.totp_pending_secret_enc = None
    user.mfa_enrolled_at = None
    clear_user_recovery_codes(db, user.id)


def verify_strong_authentication(
    user: User,
    *,
    password: str | None = None,
    totp_code: str | None = None,
) -> bool:
    """Re-authenticate with the current password or a valid TOTP code."""
    if password and verify_password(password, user.password_hash):
        return True
    if totp_code:
        secret = get_user_totp_secret(user)
        if secret is not None and verify_totp(secret, totp_code):
            return True
    return False


def mfa_required_for_user(user: User, tenant_settings: TenantSettings) -> bool:
    if not tenant_settings.mfa_enabled:
        return False
    return user.role.value in tenant_settings.mfa_required_roles


def user_has_totp(user: User) -> bool:
    return bool(user.totp_secret_enc)


def get_user_totp_secret(user: User) -> str | None:
    if not user.totp_secret_enc:
        return None
    return decrypt_secret(user.totp_secret_enc)


def get_pending_totp_secret(user: User) -> str | None:
    if not user.totp_pending_secret_enc:
        return None
    return decrypt_secret(user.totp_pending_secret_enc)


def validate_mfa_roles(roles: list[str]) -> list[str]:
    invalid = sorted(set(roles) - _VALID_MFA_ROLES)
    if invalid:
        raise ValueError(f"invalid mfaRequiredRoles: {invalid}")
    return roles
