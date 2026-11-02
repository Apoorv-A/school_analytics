"""Intervention case queue API for at-risk follow-up."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.analytics import queries
from app.db import get_db
from app.deps import AccessScope, get_access_scope, require_roles
from app.intervention_events import list_case_events, record_intervention_event
from app.models import (
    ActionTask,
    ActionTaskStatus,
    ActionTaskVisibility,
    InterventionCase,
    InterventionContact,
    InterventionContactType,
    InterventionEvent,
    InterventionEventType,
    InterventionPriority,
    InterventionStatus,
    Role,
    Student,
    User,
)
from app.schemas import (
    ActionTaskComplete,
    ActionTaskCreate,
    ActionTaskUpdate,
    FilterParams,
)
from app.tenant.features import require_interventions_enabled

router = APIRouter(prefix="/api/interventions", tags=["interventions"])

_ACTIVE_STATUSES = frozenset(
    {InterventionStatus.OPEN, InterventionStatus.IN_PROGRESS}
)
_TERMINAL_TASK_STATUSES = frozenset(
    {ActionTaskStatus.COMPLETED, ActionTaskStatus.CANCELLED}
)


class InterventionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    student_id: int = Field(ge=1, le=2_000_000_000)
    signal_source: str = Field(default="at_risk_analytics", min_length=1, max_length=80)
    reason_summary: str | None = Field(default=None, max_length=4000)
    priority: InterventionPriority | None = None
    due_date: date | None = None
    owner_user_id: int | None = Field(default=None, ge=1, le=2_000_000_000)


class InterventionUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: InterventionStatus | None = None
    owner_user_id: int | None = Field(default=None, ge=1, le=2_000_000_000)
    due_date: date | None = None
    notes_summary: str | None = Field(default=None, max_length=4000)
    clear_owner: bool = False
    clear_due_date: bool = False


class ContactCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    contact_type: InterventionContactType = InterventionContactType.PHONE
    notes: str = Field(min_length=1, max_length=4000)
    contacted_at: datetime | None = None


def _priority_from_risk(level: str) -> InterventionPriority:
    if level == "high":
        return InterventionPriority.HIGH
    if level == "watch":
        return InterventionPriority.MEDIUM
    return InterventionPriority.LOW


def _standing_for_student(
    db: Session, scope: AccessScope, student: Student
) -> queries.StudentStanding | None:
    filters = FilterParams(
        academic_year_id=scope.academic_year_id,
        section_id=student.section_id,
        student_id=student.id,
    )
    for standing in queries.student_standings(db, filters, scope):
        if standing.student_id == student.id:
            return standing
    return None


def _student_label(student: Student) -> str:
    section = student.section
    return (
        f"{student.full_name} ({section.grade.name} {section.name}, "
        f"Roll {student.roll_no})"
    )


def _serialize_event(event: InterventionEvent) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type.value,
        "actor_user_id": event.actor_user_id,
        "metadata": event.event_metadata,
        "created_at": event.created_at.isoformat(),
    }


def _serialize_task(task: ActionTask) -> dict:
    return {
        "id": task.id,
        "intervention_case_id": task.intervention_case_id,
        "title": task.title,
        "description": task.description,
        "assigned_to_user_id": task.assigned_to_user_id,
        "created_by_user_id": task.created_by_user_id,
        "visibility": task.visibility.value,
        "status": task.status.value,
        "priority": task.priority.value,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "completion_note": task.completion_note,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _can_view_task(task: ActionTask, scope: AccessScope) -> bool:
    if task.visibility is ActionTaskVisibility.STAFF:
        return True
    if scope.user.role is Role.ADMIN:
        return True
    user_id = scope.user.id
    return task.assigned_to_user_id == user_id or task.created_by_user_id == user_id


def _serialize_contact(contact: InterventionContact) -> dict:
    return {
        "id": contact.id,
        "contact_type": contact.contact_type.value,
        "notes": contact.notes,
        "contacted_at": contact.contacted_at.isoformat(),
        "created_by_user_id": contact.created_by_user_id,
        "created_at": contact.created_at.isoformat(),
    }


def _serialize_case(
    case: InterventionCase,
    *,
    include_contacts: bool = False,
    include_tasks: bool = False,
    scope: AccessScope | None = None,
) -> dict:
    student = case.student
    payload = {
        "id": case.id,
        "student_id": case.student_id,
        "student_name": student.full_name if student else None,
        "student_label": _student_label(student) if student else None,
        "section_id": student.section_id if student else None,
        "signal_source": case.signal_source,
        "reason_summary": case.reason_summary,
        "priority": case.priority.value,
        "status": case.status.value,
        "owner_user_id": case.owner_user_id,
        "due_date": case.due_date.isoformat() if case.due_date else None,
        "notes_summary": case.notes_summary,
        "created_at": case.created_at.isoformat(),
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        "created_by_user_id": case.created_by_user_id,
    }
    if include_contacts:
        payload["contacts"] = [_serialize_contact(c) for c in case.contacts]
    if include_tasks and scope is not None:
        payload["tasks"] = [
            _serialize_task(task)
            for task in case.tasks
            if _can_view_task(task, scope)
        ]
    return payload


def _get_case_or_404(
    db: Session,
    scope: AccessScope,
    case_id: int,
    *,
    with_contacts: bool = False,
    with_tasks: bool = False,
) -> InterventionCase:
    stmt = select(InterventionCase).where(
        InterventionCase.id == case_id,
        InterventionCase.tenant_id == scope.tenant_id,
    )
    options = [joinedload(InterventionCase.student).joinedload(Student.section)]
    if with_contacts:
        options.append(joinedload(InterventionCase.contacts))
    if with_tasks:
        options.append(joinedload(InterventionCase.tasks))
    stmt = stmt.options(*options)
    case = db.scalars(stmt).unique().first()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intervention case not found."
        )
    scope.assert_student(case.student_id)
    return case


def _validate_staff_user(
    db: Session, scope: AccessScope, user_id: int | None, *, label: str
) -> None:
    if user_id is None:
        return
    user = db.get(User, user_id)
    if user is None or user.tenant_id != scope.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found."
        )
    if user.role not in (Role.ADMIN, Role.TEACHER):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} must be a teacher or administrator.",
        )


def _validate_owner(db: Session, scope: AccessScope, owner_user_id: int | None) -> None:
    _validate_staff_user(db, scope, owner_user_id, label="Owner")


def _get_task_or_404(
    db: Session, scope: AccessScope, case_id: int, task_id: int
) -> ActionTask:
    task = db.scalars(
        select(ActionTask).where(
            ActionTask.id == task_id,
            ActionTask.intervention_case_id == case_id,
            ActionTask.tenant_id == scope.tenant_id,
        )
    ).first()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
        )
    if not _can_view_task(task, scope):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
        )
    return task


@router.get("")
def list_interventions(
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
    status_filter: InterventionStatus | None = Query(default=None, alias="status"),
    section_id: int | None = Query(default=None, ge=1, le=2_000_000_000),
) -> dict:
    require_interventions_enabled()
    if section_id is not None:
        scope.assert_section(section_id)

    stmt = (
        select(InterventionCase)
        .where(InterventionCase.tenant_id == scope.tenant_id)
        .options(
            joinedload(InterventionCase.student).joinedload(Student.section),
        )
        .order_by(
            InterventionCase.status,
            InterventionCase.priority.desc(),
            InterventionCase.created_at.desc(),
        )
    )
    if status_filter is not None:
        stmt = stmt.where(InterventionCase.status == status_filter)
    if section_id is not None:
        stmt = stmt.join(Student).where(Student.section_id == section_id)
    elif scope.section_ids is not None:
        stmt = stmt.join(Student).where(Student.section_id.in_(scope.section_ids))

    cases = db.scalars(stmt).unique().all()
    return {
        "cases": [_serialize_case(case) for case in cases],
        "count": len(cases),
    }


@router.get("/{case_id}/events")
def list_intervention_events(
    case_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    _get_case_or_404(db, scope, case_id)
    events = list_case_events(db, scope.tenant_id, case_id)
    return {
        "events": [_serialize_event(event) for event in events],
        "count": len(events),
    }


@router.get("/{case_id}")
def get_intervention(
    case_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    case = _get_case_or_404(db, scope, case_id, with_contacts=True, with_tasks=True)
    return _serialize_case(
        case, include_contacts=True, include_tasks=True, scope=scope
    )


@router.post("")
def create_intervention(
    payload: InterventionCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    scope.assert_student(payload.student_id)
    student = db.get(Student, payload.student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )
    scope.assert_tenant_record(student.tenant_id, "Student")

    existing = db.scalars(
        select(InterventionCase.id).where(
            InterventionCase.tenant_id == scope.tenant_id,
            InterventionCase.student_id == student.id,
            InterventionCase.status.in_(_ACTIVE_STATUSES),
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An open intervention case already exists for this student.",
        )

    standing = _standing_for_student(db, scope, student)
    if standing is None or not standing.risk.is_at_risk:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Student is not in the at-risk cohort under current filters.",
        )

    _validate_owner(db, scope, payload.owner_user_id)

    reason = payload.reason_summary
    if not reason:
        reason = "; ".join(standing.risk.reasons) or standing.risk.label
    priority = payload.priority or _priority_from_risk(standing.risk.level)

    case = InterventionCase(
        tenant_id=scope.tenant_id,
        student_id=student.id,
        signal_source=payload.signal_source,
        reason_summary=reason,
        priority=priority,
        status=InterventionStatus.OPEN,
        owner_user_id=payload.owner_user_id,
        due_date=payload.due_date,
        created_by_user_id=scope.user.id,
    )
    db.add(case)
    db.flush()
    record_intervention_event(
        db,
        tenant_id=scope.tenant_id,
        intervention_case_id=case.id,
        event_type=InterventionEventType.CASE_CREATED,
        actor_user_id=scope.user.id,
        metadata={
            "student_id": student.id,
            "priority": priority.value,
            "signal_source": payload.signal_source,
            "owner_user_id": payload.owner_user_id,
        },
    )
    db.commit()
    db.refresh(case)
    case = _get_case_or_404(db, scope, case.id)
    return _serialize_case(case)


@router.patch("/{case_id}")
def update_intervention(
    case_id: int,
    payload: InterventionUpdate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    case = _get_case_or_404(db, scope, case_id)
    prior_owner_id = case.owner_user_id
    prior_status = case.status

    if payload.clear_owner:
        case.owner_user_id = None
    elif payload.owner_user_id is not None:
        _validate_owner(db, scope, payload.owner_user_id)
        case.owner_user_id = payload.owner_user_id

    if payload.clear_due_date:
        case.due_date = None
    elif payload.due_date is not None:
        case.due_date = payload.due_date

    if payload.notes_summary is not None:
        case.notes_summary = payload.notes_summary

    if payload.status is not None:
        case.status = payload.status
        if payload.status is InterventionStatus.RESOLVED:
            case.resolved_at = datetime.now(UTC)
        elif case.resolved_at is not None:
            case.resolved_at = None

    if case.owner_user_id != prior_owner_id:
        record_intervention_event(
            db,
            tenant_id=scope.tenant_id,
            intervention_case_id=case.id,
            event_type=InterventionEventType.OWNER_CHANGED,
            actor_user_id=scope.user.id,
            metadata={
                "from_owner_user_id": prior_owner_id,
                "to_owner_user_id": case.owner_user_id,
            },
        )

    if payload.status is not None and case.status != prior_status:
        record_intervention_event(
            db,
            tenant_id=scope.tenant_id,
            intervention_case_id=case.id,
            event_type=InterventionEventType.STATUS_CHANGED,
            actor_user_id=scope.user.id,
            metadata={
                "from_status": prior_status.value,
                "to_status": case.status.value,
            },
        )
        if case.status is InterventionStatus.RESOLVED:
            record_intervention_event(
                db,
                tenant_id=scope.tenant_id,
                intervention_case_id=case.id,
                event_type=InterventionEventType.CASE_CLOSED,
                actor_user_id=scope.user.id,
                metadata={"status": case.status.value},
            )

    db.commit()
    db.refresh(case)
    case = _get_case_or_404(db, scope, case.id)
    return _serialize_case(case)


@router.post("/{case_id}/contacts")
def add_contact(
    case_id: int,
    payload: ContactCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    case = _get_case_or_404(db, scope, case_id)
    contacted_at = payload.contacted_at or datetime.now(UTC)
    contact = InterventionContact(
        tenant_id=scope.tenant_id,
        case_id=case.id,
        contact_type=payload.contact_type,
        notes=payload.notes,
        contacted_at=contacted_at,
        created_by_user_id=scope.user.id,
    )
    db.add(contact)
    prior_status = case.status
    if case.status is InterventionStatus.OPEN:
        case.status = InterventionStatus.IN_PROGRESS
    db.flush()
    record_intervention_event(
        db,
        tenant_id=scope.tenant_id,
        intervention_case_id=case.id,
        event_type=InterventionEventType.GUARDIAN_CONTACTED,
        actor_user_id=scope.user.id,
        metadata={
            "contact_id": contact.id,
            "contact_type": payload.contact_type.value,
        },
    )
    if case.status != prior_status:
        record_intervention_event(
            db,
            tenant_id=scope.tenant_id,
            intervention_case_id=case.id,
            event_type=InterventionEventType.STATUS_CHANGED,
            actor_user_id=scope.user.id,
            metadata={
                "from_status": prior_status.value,
                "to_status": case.status.value,
            },
        )
    db.commit()
    db.refresh(contact)
    return _serialize_contact(contact)


@router.get("/{case_id}/tasks")
def list_tasks(
    case_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    case = _get_case_or_404(db, scope, case_id, with_tasks=True)
    visible = [task for task in case.tasks if _can_view_task(task, scope)]
    return {
        "tasks": [_serialize_task(task) for task in visible],
        "count": len(visible),
    }


@router.post("/{case_id}/tasks")
def create_task(
    case_id: int,
    payload: ActionTaskCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    case = _get_case_or_404(db, scope, case_id)
    _validate_staff_user(
        db, scope, payload.assigned_to_user_id, label="Assignee"
    )
    task = ActionTask(
        tenant_id=scope.tenant_id,
        intervention_case_id=case.id,
        title=payload.title,
        description=payload.description,
        assigned_to_user_id=payload.assigned_to_user_id,
        created_by_user_id=scope.user.id,
        visibility=payload.visibility,
        status=ActionTaskStatus.OPEN,
        priority=payload.priority,
        due_at=payload.due_at,
    )
    db.add(task)
    db.flush()
    record_intervention_event(
        db,
        tenant_id=scope.tenant_id,
        intervention_case_id=case.id,
        event_type=InterventionEventType.TASK_CREATED,
        actor_user_id=scope.user.id,
        metadata={
            "task_id": task.id,
            "title": task.title,
            "priority": task.priority.value,
            "assigned_to_user_id": task.assigned_to_user_id,
            "visibility": task.visibility.value,
        },
    )
    db.commit()
    db.refresh(task)
    return _serialize_task(task)


@router.patch("/{case_id}/tasks/{task_id}")
def update_task(
    case_id: int,
    task_id: int,
    payload: ActionTaskUpdate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    _get_case_or_404(db, scope, case_id)
    task = _get_task_or_404(db, scope, case_id, task_id)

    if payload.title is not None:
        task.title = payload.title
    if payload.description is not None:
        task.description = payload.description
    if payload.visibility is not None:
        task.visibility = payload.visibility
    if payload.priority is not None:
        task.priority = payload.priority
    if payload.clear_assignee:
        task.assigned_to_user_id = None
    elif payload.assigned_to_user_id is not None:
        _validate_staff_user(
            db, scope, payload.assigned_to_user_id, label="Assignee"
        )
        task.assigned_to_user_id = payload.assigned_to_user_id
    if payload.clear_due_at:
        task.due_at = None
    elif payload.due_at is not None:
        task.due_at = payload.due_at
    if payload.status is not None:
        if (
            task.status in _TERMINAL_TASK_STATUSES
            and payload.status not in _TERMINAL_TASK_STATUSES
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Completed or cancelled tasks cannot be reopened.",
            )
        task.status = payload.status
        if payload.status is ActionTaskStatus.COMPLETED:
            task.completed_at = datetime.now(UTC)
        elif task.completed_at is not None and payload.status is not ActionTaskStatus.COMPLETED:
            task.completed_at = None
            task.completion_note = None

    db.commit()
    db.refresh(task)
    return _serialize_task(task)


@router.post("/{case_id}/tasks/{task_id}/complete")
def complete_task(
    case_id: int,
    task_id: int,
    payload: ActionTaskComplete,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    _staff: object = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict:
    require_interventions_enabled()
    _get_case_or_404(db, scope, case_id)
    task = _get_task_or_404(db, scope, case_id, task_id)
    if task.status in _TERMINAL_TASK_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Task is already completed or cancelled.",
        )

    prior_status = task.status
    task.status = ActionTaskStatus.COMPLETED
    task.completed_at = datetime.now(UTC)
    task.completion_note = payload.completion_note
    db.flush()
    record_intervention_event(
        db,
        tenant_id=scope.tenant_id,
        intervention_case_id=case_id,
        event_type=InterventionEventType.TASK_COMPLETED,
        actor_user_id=scope.user.id,
        metadata={
            "task_id": task.id,
            "from_status": prior_status.value,
            "to_status": task.status.value,
        },
    )
    db.commit()
    db.refresh(task)
    return _serialize_task(task)
