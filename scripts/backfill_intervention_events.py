"""Backfill historical intervention timeline events for existing cases.

Creates ``case_created``, ``guardian_contacted``, and ``case_closed`` events for
cases and contacts that predate P1-1 event emission. Idempotent via ``backfill_key``
metadata and existing event checks.

Usage::

    PYTHONPATH=. python3 scripts/backfill_intervention_events.py
    PYTHONPATH=. python3 scripts/backfill_intervention_events.py --dry-run
    PYTHONPATH=. python3 scripts/backfill_intervention_events.py --tenant sunrise
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import SessionLocal
from app.intervention_events import record_intervention_event
from app.models import (
    InterventionCase,
    InterventionEvent,
    InterventionEventType,
    InterventionStatus,
    Tenant,
)

BACKFILL_SOURCE = "backfill_intervention_events"


def backfill_key(kind: str, entity_id: int) -> str:
    return f"backfill:{kind}:{entity_id}"


@dataclass
class BackfillStats:
    case_created: int = 0
    guardian_contacted: int = 0
    case_closed: int = 0

    @property
    def total(self) -> int:
        return self.case_created + self.guardian_contacted + self.case_closed


def _events_by_case(
    db: Session, tenant_ids: list[uuid.UUID] | None
) -> dict[int, list[InterventionEvent]]:
    stmt = select(InterventionEvent)
    if tenant_ids is not None:
        stmt = stmt.where(InterventionEvent.tenant_id.in_(tenant_ids))
    grouped: dict[int, list[InterventionEvent]] = {}
    for event in db.scalars(stmt):
        grouped.setdefault(event.intervention_case_id, []).append(event)
    return grouped


def _has_backfill_key(events: list[InterventionEvent], key: str) -> bool:
    for event in events:
        metadata = event.event_metadata or {}
        if metadata.get("backfill_key") == key:
            return True
    return False


def _has_event_type(events: list[InterventionEvent], event_type: InterventionEventType) -> bool:
    return any(event.event_type == event_type for event in events)


def _has_guardian_contact_event(
    events: list[InterventionEvent], contact_id: int
) -> bool:
    key = backfill_key("guardian_contacted", contact_id)
    if _has_backfill_key(events, key):
        return True
    for event in events:
        if event.event_type is not InterventionEventType.GUARDIAN_CONTACTED:
            continue
        metadata = event.event_metadata or {}
        if metadata.get("contact_id") == contact_id:
            return True
    return False


def _record_backfill_event(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    intervention_case_id: int,
    event_type: InterventionEventType,
    created_at: datetime,
    actor_user_id: int | None,
    metadata: dict,
    dry_run: bool,
) -> InterventionEvent | None:
    payload = {
        **metadata,
        "backfill_source": BACKFILL_SOURCE,
    }
    if dry_run:
        return None
    event = record_intervention_event(
        db,
        tenant_id=tenant_id,
        intervention_case_id=intervention_case_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        metadata=payload,
    )
    event.created_at = created_at
    return event


def backfill_intervention_events(
    db: Session,
    *,
    tenant_key: str | None = None,
    dry_run: bool = False,
) -> BackfillStats:
    """Backfill missing timeline events for intervention cases in scope."""
    stats = BackfillStats()
    tenant_ids: list[uuid.UUID] | None = None
    if tenant_key is not None:
        tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
        tenant_ids = [tenant.id]

    case_stmt = select(InterventionCase).options(joinedload(InterventionCase.contacts))
    if tenant_ids is not None:
        case_stmt = case_stmt.where(InterventionCase.tenant_id.in_(tenant_ids))
    cases = db.scalars(case_stmt).unique().all()

    events_by_case = _events_by_case(db, tenant_ids)

    for case in cases:
        case_events = events_by_case.get(case.id, [])

        created_key = backfill_key("case_created", case.id)
        missing_created = (
            not _has_event_type(case_events, InterventionEventType.CASE_CREATED)
            and not _has_backfill_key(case_events, created_key)
        )
        if missing_created:
            metadata: dict[str, object] = {
                "backfill_key": created_key,
                "student_id": case.student_id,
                "status": case.status.value,
            }
            if case.owner_user_id is not None:
                metadata["owner_user_id"] = case.owner_user_id
            _record_backfill_event(
                db,
                tenant_id=case.tenant_id,
                intervention_case_id=case.id,
                event_type=InterventionEventType.CASE_CREATED,
                created_at=case.created_at,
                actor_user_id=case.created_by_user_id,
                metadata=metadata,
                dry_run=dry_run,
            )
            stats.case_created += 1

        for contact in case.contacts:
            if _has_guardian_contact_event(case_events, contact.id):
                continue
            contact_key = backfill_key("guardian_contacted", contact.id)
            _record_backfill_event(
                db,
                tenant_id=case.tenant_id,
                intervention_case_id=case.id,
                event_type=InterventionEventType.GUARDIAN_CONTACTED,
                created_at=contact.created_at,
                actor_user_id=contact.created_by_user_id,
                metadata={
                    "backfill_key": contact_key,
                    "contact_id": contact.id,
                    "contact_type": contact.contact_type.value,
                },
                dry_run=dry_run,
            )
            stats.guardian_contacted += 1

        if case.status is InterventionStatus.RESOLVED:
            closed_key = backfill_key("case_closed", case.id)
            missing_closed = (
                not _has_event_type(case_events, InterventionEventType.CASE_CLOSED)
                and not _has_backfill_key(case_events, closed_key)
            )
            if missing_closed:
                closed_at = case.resolved_at or case.created_at
                _record_backfill_event(
                    db,
                    tenant_id=case.tenant_id,
                    intervention_case_id=case.id,
                    event_type=InterventionEventType.CASE_CLOSED,
                    created_at=closed_at,
                    actor_user_id=case.created_by_user_id,
                    metadata={
                        "backfill_key": closed_key,
                        "status": case.status.value,
                    },
                    dry_run=dry_run,
                )
                stats.case_closed += 1

    if not dry_run:
        db.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill intervention timeline events for legacy cases"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without writing to the database",
    )
    parser.add_argument(
        "--tenant",
        metavar="KEY",
        help="limit backfill to a single tenant key",
    )
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        stats = backfill_intervention_events(
            db, tenant_key=args.tenant, dry_run=args.dry_run
        )

    mode = "would backfill" if args.dry_run else "backfilled"
    scope = f"tenant={args.tenant}" if args.tenant else "all tenants"
    print(
        f"{mode} {stats.total} event(s) ({scope}): "
        f"case_created={stats.case_created}, "
        f"guardian_contacted={stats.guardian_contacted}, "
        f"case_closed={stats.case_closed}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
