"""F07: guardian unread published-remark notices (in-app badge + drawer)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.deps import AccessScope
from app.models import (
    GuardianRemarkRead,
    Remark,
    RemarkVisibility,
    Role,
    Student,
    Teacher,
    Term,
    User,
)

PREVIEW_LIMIT = 25
BODY_PREVIEW_CHARS = 280


def merge_parent_remark_notices_shell(
    context: dict,
    scope: AccessScope,
    *,
    remarks_enabled: bool,
    parent_portal_enabled: bool,
) -> None:
    if (
        scope.role is Role.PARENT
        and remarks_enabled
        and parent_portal_enabled
    ):
        context["parent_remark_notices"] = {"enabled": True}


def _child_student_ids(scope: AccessScope) -> frozenset[int]:
    if scope.role is not Role.PARENT:
        return frozenset()
    return scope.student_ids or frozenset()


def _unread_base_stmt(
    scope: AccessScope,
) -> select:
    child_ids = _child_student_ids(scope)
    if not child_ids:
        return select(Remark).where(Remark.id < 0)

    read_ids = select(GuardianRemarkRead.remark_id).where(
        GuardianRemarkRead.guardian_user_id == scope.user.id,
        GuardianRemarkRead.tenant_id == scope.tenant_id,
    )
    return (
        select(Remark)
        .where(
            Remark.tenant_id == scope.tenant_id,
            Remark.visibility == RemarkVisibility.PUBLISHED,
            Remark.student_id.in_(child_ids),
            Remark.id.not_in(read_ids),
        )
    )


def unread_remark_count(db: Session, scope: AccessScope) -> int:
    if scope.role is not Role.PARENT:
        return 0
    base = _unread_base_stmt(scope)
    return db.scalar(select(func.count()).select_from(base.subquery())) or 0


def list_unread_remark_previews(db: Session, scope: AccessScope) -> list[dict[str, object]]:
    if scope.role is not Role.PARENT:
        return []

    stmt = (
        _unread_base_stmt(scope)
        .add_columns(Student.full_name, Term.name, User.full_name)
        .join(Student, Remark.student_id == Student.id)
        .outerjoin(Term, Remark.term_id == Term.id)
        .outerjoin(Teacher, Remark.teacher_id == Teacher.id)
        .outerjoin(User, Teacher.user_id == User.id)
        .order_by(
            Remark.published_at.desc().nullslast(),
            Remark.created_at.desc(),
            Remark.id.desc(),
        )
        .limit(PREVIEW_LIMIT)
    )
    items: list[dict[str, object]] = []
    for remark, student_name, term_name, teacher_name in db.execute(stmt).all():
        body = remark.body.strip()
        if len(body) > BODY_PREVIEW_CHARS:
            body = body[: BODY_PREVIEW_CHARS - 1].rstrip() + "\u2026"
        published = remark.published_at or remark.created_at
        items.append(
            {
                "id": remark.id,
                "student_id": remark.student_id,
                "student_name": student_name,
                "category": remark.category.value,
                "category_label": remark.category.label,
                "body_preview": body,
                "term": term_name or "\u2014",
                "teacher": teacher_name or "School office",
                "published_at": published.isoformat(),
            }
        )
    return items


def _remark_ids_in_guardian_scope(
    db: Session, scope: AccessScope, remark_ids: list[int]
) -> list[int]:
    if not remark_ids or scope.role is not Role.PARENT:
        return []
    child_ids = _child_student_ids(scope)
    if not child_ids:
        return []
    rows = db.scalars(
        select(Remark.id).where(
            Remark.tenant_id == scope.tenant_id,
            Remark.id.in_(remark_ids),
            Remark.visibility == RemarkVisibility.PUBLISHED,
            Remark.student_id.in_(child_ids),
        )
    ).all()
    return list(rows)


def mark_remarks_read(
    db: Session,
    scope: AccessScope,
    *,
    remark_ids: list[int] | None = None,
    mark_all: bool = False,
) -> int:
    """Record read state; returns how many new rows were inserted."""
    if scope.role is not Role.PARENT:
        return 0

    if mark_all:
        target_ids = [r.id for r in db.scalars(_unread_base_stmt(scope)).all()]
    elif remark_ids:
        target_ids = _remark_ids_in_guardian_scope(db, scope, remark_ids)
    else:
        return 0

    if not target_ids:
        return 0

    existing = set(
        db.scalars(
            select(GuardianRemarkRead.remark_id).where(
                GuardianRemarkRead.guardian_user_id == scope.user.id,
                GuardianRemarkRead.remark_id.in_(target_ids),
            )
        ).all()
    )
    now = datetime.now(UTC)
    inserted = 0
    for rid in target_ids:
        if rid in existing:
            continue
        db.add(
            GuardianRemarkRead(
                tenant_id=scope.tenant_id,
                guardian_user_id=scope.user.id,
                remark_id=rid,
                read_at=now,
            )
        )
        inserted += 1
    if inserted:
        db.commit()
    return inserted
