"""Resolve academic terms for teacher remarks when none is chosen."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.import_.apply import _current_academic_year, _resolve_term_for_date
from app.models import Term


def infer_remark_term_id(
    db: Session, tenant_id: uuid.UUID, school_id: int, when: datetime
) -> int | None:
    """Pick a term for a remark written or published at ``when``.

    Teachers may leave the term unset when the portal filter is on Full year.
    Those remarks still need a term so academic-year charts can include them
    alongside term-tagged notes.
    """
    term = _resolve_term_for_date(db, tenant_id, when.date())
    if term is not None:
        return term.id
    year = _current_academic_year(db, tenant_id, school_id)
    if year is None:
        return None
    return db.scalar(
        select(Term.id)
        .where(Term.tenant_id == tenant_id, Term.academic_year_id == year.id)
        .order_by(Term.sequence.desc())
        .limit(1)
    )
