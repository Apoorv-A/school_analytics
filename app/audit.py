"""Append-only security audit events (identity and access)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SecurityAuditEvent

_FORBIDDEN_METADATA_SUBSTRINGS = (
    "password",
    "token",
    "secret",
    "email",
    "authorization",
    "cookie",
    "recovery",
    "code",
)


class SecurityAuditAction:
    LOGIN_SUCCESS = "login.success"
    LOGIN_FAILURE = "login.failure"
    LOGIN_LOCKOUT = "login.lockout"
    AUTH_THROTTLE_UNAVAILABLE = "auth.throttle.unavailable"
    LOGOUT = "logout"
    RESET_REQUESTED = "password.reset.requested"
    RESET_COMPLETED = "password.reset.completed"
    RESET_ISSUED = "password.reset.issued"
    MFA_VERIFY_SUCCESS = "mfa.verify.success"
    MFA_VERIFY_FAILURE = "mfa.verify.failure"
    MFA_ENROLLED = "mfa.enrolled"
    MFA_RESET = "mfa.reset"
    MFA_RECOVERY_CODE_USED = "mfa.recovery_code.used"
    MFA_RECOVERY_CODES_GENERATED = "mfa.recovery_codes.generated"
    SESSION_REVOKED = "session.revoked"
    WEBHOOK_AUTH_FAILURE = "webhook.auth.failure"
    IMPORT_MAPPING_MAP = "import.mapping.map"
    IMPORT_MAPPING_REJECT = "import.mapping.reject"
    IMPORT_CONFLICT_RESOLVE = "import.conflict.resolve"
    USER_INVITED = "user.invited"
    EXPORT_DOWNLOAD = "data.export.download"
    IMPORT_APPLY = "data.import.apply"
    CHART_ACCESS = "data.chart.access"
    REMARK_PUBLISHED = "remark.published"


class SecurityAuditTarget:
    USER = "user"
    SESSION = "session"
    WEBHOOK = "webhook"
    IMPORT_MAPPING = "import_mapping"
    IMPORT_CONFLICT = "import_conflict"
    EXPORT = "export"
    IMPORT = "import"
    CHART = "chart"
    REMARK = "remark"


SENSITIVE_CHART_KEYS = frozenset(
    {
        "student.insights",
        "section.at_risk",
        "school.at_risk",
        "school.teacher_effectiveness",
    }
)


def correlation_id_from_request(request: Request | None) -> str | None:
    """Read a caller-supplied request id or generate one for the audit row."""
    if request is not None:
        for header in ("X-Correlation-ID", "X-Request-ID", "X-Request-Id"):
            value = request.headers.get(header, "").strip()
            if value:
                return value[:128]
    return str(uuid.uuid4())


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = key.lower()
        if any(part in lowered for part in _FORBIDDEN_METADATA_SUBSTRINGS):
            continue
        if isinstance(value, str) and any(
            part in value.lower() for part in ("password", "token", "secret")
        ):
            continue
        safe[key] = value
    return safe or None


def _normalize_target_id(target_id: str | int) -> str:
    text = str(target_id).strip()
    return text[:64] if text else "unknown"


def record_security_event(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: str | int,
    actor_user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    request: Request | None = None,
) -> SecurityAuditEvent:
    """Persist one append-only security audit event."""
    event = SecurityAuditEvent(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=_normalize_target_id(target_id),
        event_metadata=_sanitize_metadata(metadata),
        correlation_id=correlation_id or correlation_id_from_request(request),
    )
    db.add(event)
    db.flush()
    return event


def recent_security_events(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int = 50,
) -> list[SecurityAuditEvent]:
    return list(
        db.scalars(
            select(SecurityAuditEvent)
            .where(SecurityAuditEvent.tenant_id == tenant_id)
            .order_by(SecurityAuditEvent.created_at.desc())
            .limit(limit)
        )
    )
