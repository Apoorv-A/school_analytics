"""Append-only intervention case event timeline."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InterventionEvent, InterventionEventType

_FORBIDDEN_METADATA_SUBSTRINGS = (
    "password",
    "token",
    "secret",
    "email",
    "notes",
    "summary",
    "phone",
    "address",
    "authorization",
    "cookie",
)


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = key.lower()
        if any(part in lowered for part in _FORBIDDEN_METADATA_SUBSTRINGS):
            continue
        if isinstance(value, str) and any(
            part in value.lower() for part in ("password", "token", "secret", "@")
        ):
            continue
        safe[key] = value
    return safe or None


def record_intervention_event(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    intervention_case_id: int,
    event_type: InterventionEventType,
    actor_user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> InterventionEvent:
    """Persist one append-only intervention timeline event."""
    event = InterventionEvent(
        tenant_id=tenant_id,
        intervention_case_id=intervention_case_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        event_metadata=_sanitize_metadata(metadata),
    )
    db.add(event)
    db.flush()
    return event


def list_case_events(
    db: Session,
    tenant_id: uuid.UUID,
    intervention_case_id: int,
) -> list[InterventionEvent]:
    return list(
        db.scalars(
            select(InterventionEvent)
            .where(
                InterventionEvent.tenant_id == tenant_id,
                InterventionEvent.intervention_case_id == intervention_case_id,
            )
            .order_by(InterventionEvent.created_at.asc())
        )
    )
