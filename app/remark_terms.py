"""Resolve academic terms for teacher remarks when none is chosen."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.import_.apply import _current_academic_year, _resolve_term_for_date
from app.models import AcademicYear, Grade, Remark, Section, Student, Term


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


def remark_placed_at() -> ColumnElement:
    """When a remark was published, or written if still a draft."""
    return func.coalesce(Remark.published_at, Remark.created_at)


def _school_id_for_remark_subquery():
    return (
        select(Grade.school_id)
        .select_from(Student)
        .join(Section, Student.section_id == Section.id)
        .join(Grade, Section.grade_id == Grade.id)
        .where(Student.id == Remark.student_id)
        .limit(1)
        .correlate(Remark)
        .scalar_subquery()
    )


def inferred_remark_term_id_expr() -> ColumnElement:
    """SQL counterpart of :func:`infer_remark_term_id` for nullable ``term_id`` rows."""
    placed = remark_placed_at()
    on_date = func.date(placed)
    date_term_predicates = (
        Term.tenant_id == Remark.tenant_id,
        Term.start_date <= on_date,
        Term.end_date >= on_date,
    )
    date_match_count = (
        select(func.count(Term.id))
        .where(*date_term_predicates)
        .correlate(Remark)
        .scalar_subquery()
    )
    sole_date_term = (
        select(Term.id)
        .where(*date_term_predicates)
        .limit(1)
        .correlate(Remark)
        .scalar_subquery()
    )
    school_id = _school_id_for_remark_subquery()
    current_year_id = (
        select(AcademicYear.id)
        .where(
            AcademicYear.tenant_id == Remark.tenant_id,
            AcademicYear.school_id == school_id,
        )
        .order_by(AcademicYear.is_current.desc(), AcademicYear.start_date.desc())
        .limit(1)
        .correlate(Remark)
        .scalar_subquery()
    )
    fallback_term = (
        select(Term.id)
        .where(
            Term.tenant_id == Remark.tenant_id,
            Term.academic_year_id == current_year_id,
        )
        .order_by(Term.sequence.desc())
        .limit(1)
        .correlate(Remark)
        .scalar_subquery()
    )
    return case((date_match_count == 1, sole_date_term), else_=fallback_term)
