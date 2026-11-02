"""Resolve latest parental consent state for policy and UI."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConsentPurpose, ConsentStatus, ParentalConsentRecord


def latest_by_purpose(
    records: list[ParentalConsentRecord],
) -> dict[ConsentPurpose, ParentalConsentRecord]:
    latest: dict[ConsentPurpose, ParentalConsentRecord] = {}
    for record in sorted(records, key=lambda row: row.recorded_at):
        latest[record.purpose] = record
    return latest


def purpose_granted(
    records: list[ParentalConsentRecord], purpose: ConsentPurpose
) -> bool:
    row = latest_by_purpose(records).get(purpose)
    return row is not None and row.status is ConsentStatus.GRANTED


def student_analytics_consent_granted(
    db: Session, tenant_id: uuid.UUID, student_id: int
) -> bool:
    records = list(
        db.scalars(
            select(ParentalConsentRecord).where(
                ParentalConsentRecord.tenant_id == tenant_id,
                ParentalConsentRecord.student_id == student_id,
                ParentalConsentRecord.purpose == ConsentPurpose.STUDENT_ANALYTICS,
            )
        )
    )
    return purpose_granted(records, ConsentPurpose.STUDENT_ANALYTICS)
