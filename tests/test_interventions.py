"""Intervention case workflow tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.analytics import queries
from app.models import (
    ActionTask,
    InterventionCase,
    InterventionContact,
    InterventionContactType,
    InterventionEvent,
    InterventionEventType,
    InterventionPriority,
    InterventionStatus,
    Role,
    Student,
    Tenant,
    User,
)
from app.schemas import FilterParams
from scripts.backfill_intervention_events import backfill_intervention_events
from seed.generate import DEMO_PASSWORD, HORIZON_HOST


def _enable_interventions(db, *, tenant_key: str = "sunrise") -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    features["interventions"] = True
    settings["features"] = features
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


def _disable_interventions(db, *, tenant_key: str = "sunrise") -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    features["interventions"] = False
    settings["features"] = features
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


def _at_risk_student(db, *, tenant_key: str = "sunrise") -> Student:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    from app.deps import AccessScope

    admin = db.scalars(
        select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN)
    ).one()
    scope = AccessScope(
        tenant_id=tenant.id,
        user=admin,
        academic_year=None,
        student_ids=None,
        section_ids=None,
        subject_ids=None,
        students=(),
    )
    from app.models import AcademicYear

    year = db.scalars(
        select(AcademicYear)
        .where(AcademicYear.tenant_id == tenant.id)
        .order_by(AcademicYear.is_current.desc())
        .limit(1)
    ).one()
    scope = AccessScope(
        tenant_id=tenant.id,
        user=admin,
        academic_year=year,
        student_ids=None,
        section_ids=None,
        subject_ids=None,
        students=(),
    )
    filters = FilterParams(academic_year_id=year.id)
    for standing in queries.student_standings(db, filters, scope):
        if standing.risk.is_at_risk:
            return db.get(Student, standing.student_id)
    pytest.skip("seed has no at-risk students")


def _at_risk_student_without_case(db, *, tenant_key: str = "sunrise") -> Student:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    from app.deps import AccessScope
    from app.models import AcademicYear

    admin = db.scalars(
        select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN)
    ).one()
    year = db.scalars(
        select(AcademicYear)
        .where(AcademicYear.tenant_id == tenant.id)
        .order_by(AcademicYear.is_current.desc())
        .limit(1)
    ).one()
    scope = AccessScope(
        tenant_id=tenant.id,
        user=admin,
        academic_year=year,
        student_ids=None,
        section_ids=None,
        subject_ids=None,
        students=(),
    )
    filters = FilterParams(academic_year_id=year.id)
    for standing in queries.student_standings(db, filters, scope):
        if not standing.risk.is_at_risk:
            continue
        existing = db.scalars(
            select(InterventionCase.id).where(
                InterventionCase.tenant_id == tenant.id,
                InterventionCase.student_id == standing.student_id,
                InterventionCase.status.in_(
                    (InterventionStatus.OPEN, InterventionStatus.IN_PROGRESS)
                ),
            )
        ).first()
        if existing is None:
            return db.get(Student, standing.student_id)
    pytest.skip("no at-risk student without an active case")


@pytest.fixture
def interventions_enabled(db):
    _enable_interventions(db)
    yield


class TestInterventionFeatureFlag:
    def test_api_hidden_when_disabled(self, admin_client):
        response = admin_client.get("/api/interventions")
        assert response.status_code == 404

    def test_page_hidden_when_disabled(self, admin_client):
        response = admin_client.get("/admin/interventions", follow_redirects=False)
        assert response.status_code == 404


class TestInterventionCrud:
    def test_admin_create_list_update_and_contact(
        self, admin_client, db, interventions_enabled
    ):
        student = _at_risk_student(db)
        create = admin_client.post(
            "/api/interventions",
            json={"student_id": student.id, "signal_source": "at_risk_analytics"},
        )
        assert create.status_code == 200
        case_id = create.json()["id"]
        assert create.json()["status"] == "open"
        assert create.json()["student_id"] == student.id

        listing = admin_client.get("/api/interventions")
        assert listing.status_code == 200
        assert any(row["id"] == case_id for row in listing.json()["cases"])

        updated = admin_client.patch(
            f"/api/interventions/{case_id}",
            json={"status": "in_progress", "notes_summary": "Parent contacted"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "in_progress"

        contact = admin_client.post(
            f"/api/interventions/{case_id}/contacts",
            json={"contact_type": "phone", "notes": "Left voicemail"},
        )
        assert contact.status_code == 200
        assert contact.json()["notes"] == "Left voicemail"

        detail = admin_client.get(f"/api/interventions/{case_id}")
        assert detail.status_code == 200
        assert len(detail.json()["contacts"]) == 1

        resolved = admin_client.patch(
            f"/api/interventions/{case_id}",
            json={"status": "resolved"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_at"] is not None

    def test_duplicate_open_case_rejected(self, admin_client, db, interventions_enabled):
        student = _at_risk_student(db)
        first = admin_client.post(
            "/api/interventions",
            json={"student_id": student.id},
        )
        assert first.status_code == 200
        second = admin_client.post(
            "/api/interventions",
            json={"student_id": student.id},
        )
        assert second.status_code == 409

    def test_non_at_risk_student_rejected(self, admin_client, db, interventions_enabled):
        from app.analytics import metrics
        from app.deps import AccessScope
        from app.models import AcademicYear

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN)
        ).one()
        year = db.scalars(
            select(AcademicYear).where(AcademicYear.tenant_id == tenant.id).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=tenant.id,
            user=admin,
            academic_year=year,
            student_ids=None,
            section_ids=None,
            subject_ids=None,
            students=(),
        )
        filters = FilterParams(academic_year_id=year.id)
        on_track = None
        for standing in queries.student_standings(db, filters, scope):
            if not standing.risk.is_at_risk and standing.average is not None:
                if metrics.assess_risk(
                    standing.average,
                    standing.term_slope,
                    standing.attendance,
                    standing.failing_subjects,
                ).level == "none":
                    on_track = db.get(Student, standing.student_id)
                    break
        if on_track is None:
            pytest.skip("no on-track student in seed")
        response = admin_client.post(
            "/api/interventions",
            json={"student_id": on_track.id},
        )
        assert response.status_code == 422


class TestInterventionScope:
    def test_teacher_can_manage_own_student(self, teacher_context, db, interventions_enabled):
        client = teacher_context["client"]
        student_id = teacher_context["own_student_id"]
        student = db.get(Student, student_id)
        from app.deps import AccessScope
        from app.models import AcademicYear, Teacher

        teacher = db.get(Teacher, teacher_context["teacher_id"])
        year = db.scalars(
            select(AcademicYear).where(AcademicYear.tenant_id == student.tenant_id).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=student.tenant_id,
            user=db.get(User, teacher.user_id),
            academic_year=year,
            student_ids=frozenset({student_id}),
            section_ids=frozenset({student.section_id}),
            subject_ids=None,
            teacher=teacher,
            students=(student,),
        )
        standing = next(
            s
            for s in queries.student_standings(
                db,
                FilterParams(academic_year_id=year.id, section_id=student.section_id),
                scope,
            )
            if s.student_id == student_id
        )
        if not standing.risk.is_at_risk:
            pytest.skip("assigned student is not at-risk in seed")

        create = client.post(
            "/api/interventions",
            json={"student_id": student_id},
        )
        assert create.status_code == 200

    def test_teacher_cannot_create_for_outside_student(
        self, teacher_context, interventions_enabled
    ):
        outside = teacher_context.get("outside_student_id")
        if outside is None:
            pytest.skip("no outside student")
        response = teacher_context["client"].post(
            "/api/interventions",
            json={"student_id": outside},
        )
        assert response.status_code == 404

    def test_parent_cannot_access_api(self, parent_context, interventions_enabled):
        response = parent_context["client"].get("/api/interventions")
        assert response.status_code == 403


class TestInterventionTenantIsolation:
    def test_horizon_admin_cannot_read_sunrise_case(
        self, admin_client, db, interventions_enabled
    ):
        from fastapi.testclient import TestClient

        from app.main import app

        student = _at_risk_student_without_case(db, tenant_key="sunrise")
        created = admin_client.post(
            "/api/interventions",
            json={"student_id": student.id},
        )
        assert created.status_code == 200
        case_id = created.json()["id"]

        horizon = db.scalars(select(Tenant).where(Tenant.key == "horizon")).one()
        email = db.scalars(
            select(User.email).where(
                User.tenant_id == horizon.id, User.role == Role.ADMIN
            )
        ).one()
        _enable_interventions(db, tenant_key="horizon")
        horizon_client = TestClient(app, headers={"Host": HORIZON_HOST}, follow_redirects=False)
        login = horizon_client.post(
            "/login",
            data={"email": email, "password": DEMO_PASSWORD},
        )
        assert login.status_code == 303
        response = horizon_client.get(f"/api/interventions/{case_id}")
        assert response.status_code == 404

        sunrise_case = db.get(InterventionCase, case_id)
        assert sunrise_case is not None
        assert sunrise_case.tenant_id != horizon.id


class TestInterventionEvents:
    def test_events_emitted_on_create_update_and_contact(
        self, admin_client, db, interventions_enabled
    ):
        student = _at_risk_student_without_case(db)
        create = admin_client.post(
            "/api/interventions",
            json={"student_id": student.id, "signal_source": "at_risk_analytics"},
        )
        assert create.status_code == 200
        case_id = create.json()["id"]

        events = admin_client.get(f"/api/interventions/{case_id}/events")
        assert events.status_code == 200
        types = [row["event_type"] for row in events.json()["events"]]
        assert types == ["case_created"]
        assert events.json()["events"][0]["metadata"]["student_id"] == student.id
        assert "reason_summary" not in (events.json()["events"][0]["metadata"] or {})

        updated = admin_client.patch(
            f"/api/interventions/{case_id}",
            json={"status": "in_progress"},
        )
        assert updated.status_code == 200

        contact = admin_client.post(
            f"/api/interventions/{case_id}/contacts",
            json={"contact_type": "phone", "notes": "Parent called back"},
        )
        assert contact.status_code == 200

        resolved = admin_client.patch(
            f"/api/interventions/{case_id}",
            json={"status": "resolved"},
        )
        assert resolved.status_code == 200

        timeline = admin_client.get(f"/api/interventions/{case_id}/events").json()
        types = [row["event_type"] for row in timeline["events"]]
        assert types[0] == "case_created"
        assert "status_changed" in types
        assert "guardian_contacted" in types
        assert types[-1] == "case_closed"
        for row in timeline["events"]:
            metadata = row.get("metadata") or {}
            assert "notes" not in metadata
            assert "Parent called back" not in str(metadata)

    def test_parent_cannot_list_events(
        self, admin_client, parent_context, db, interventions_enabled
    ):
        student = _at_risk_student_without_case(db)
        create = admin_client.post(
            "/api/interventions",
            json={"student_id": student.id},
        )
        assert create.status_code == 200
        case_id = create.json()["id"]
        response = parent_context["client"].get(
            f"/api/interventions/{case_id}/events"
        )
        assert response.status_code == 403


class TestInterventionEventTenantIsolation:
    def test_horizon_admin_cannot_read_sunrise_events(
        self, admin_client, db, interventions_enabled
    ):
        from fastapi.testclient import TestClient

        from app.main import app

        student = _at_risk_student_without_case(db, tenant_key="sunrise")
        created = admin_client.post(
            "/api/interventions",
            json={"student_id": student.id},
        )
        assert created.status_code == 200
        case_id = created.json()["id"]
        assert admin_client.get(f"/api/interventions/{case_id}/events").json()["count"] >= 1

        horizon = db.scalars(select(Tenant).where(Tenant.key == "horizon")).one()
        email = db.scalars(
            select(User.email).where(
                User.tenant_id == horizon.id, User.role == Role.ADMIN
            )
        ).one()
        _enable_interventions(db, tenant_key="horizon")
        horizon_client = TestClient(app, headers={"Host": HORIZON_HOST}, follow_redirects=False)
        login = horizon_client.post(
            "/login",
            data={"email": email, "password": DEMO_PASSWORD},
        )
        assert login.status_code == 303
        response = horizon_client.get(f"/api/interventions/{case_id}/events")
        assert response.status_code == 404

        stored = db.scalars(
            select(InterventionEvent).where(
                InterventionEvent.intervention_case_id == case_id
            )
        ).all()
        assert stored
        assert all(event.tenant_id != horizon.id for event in stored)


class TestInterventionEventBackfill:
    def _legacy_case(
        self,
        db,
        *,
        tenant_key: str = "sunrise",
        status: InterventionStatus = InterventionStatus.RESOLVED,
        with_contact: bool = True,
    ) -> InterventionCase:
        tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN)
        ).one()
        student = _at_risk_student_without_case(db, tenant_key=tenant_key)
        opened_at = datetime(2024, 9, 1, 10, 0, tzinfo=UTC)
        case = InterventionCase(
            tenant_id=tenant.id,
            student_id=student.id,
            signal_source="at_risk_analytics",
            reason_summary="Legacy case for backfill test",
            priority=InterventionPriority.MEDIUM,
            status=status,
            owner_user_id=admin.id,
            notes_summary="Sensitive notes must not appear in events",
            created_by_user_id=admin.id,
            created_at=opened_at,
        )
        if status is InterventionStatus.RESOLVED:
            case.resolved_at = datetime(2024, 9, 3, 15, 0, tzinfo=UTC)
        db.add(case)
        db.flush()
        if with_contact:
            db.add(
                InterventionContact(
                    tenant_id=tenant.id,
                    case_id=case.id,
                    contact_type=InterventionContactType.PHONE,
                    notes="Private contact notes",
                    contacted_at=opened_at,
                    created_by_user_id=admin.id,
                    created_at=datetime(2024, 9, 2, 11, 0, tzinfo=UTC),
                )
            )
        db.commit()
        db.refresh(case)
        return case

    def test_backfill_is_idempotent_and_sanitizes_metadata(self, db):
        case = self._legacy_case(db)
        assert (
            db.scalars(
                select(InterventionEvent).where(
                    InterventionEvent.intervention_case_id == case.id
                )
            ).all()
            == []
        )

        first = backfill_intervention_events(db, tenant_key="sunrise")
        assert first.case_created == 1
        assert first.guardian_contacted == 1
        assert first.case_closed == 1

        events = db.scalars(
            select(InterventionEvent)
            .where(InterventionEvent.intervention_case_id == case.id)
            .order_by(InterventionEvent.created_at.asc())
        ).all()
        types = [event.event_type for event in events]
        assert types == [
            InterventionEventType.CASE_CREATED,
            InterventionEventType.GUARDIAN_CONTACTED,
            InterventionEventType.CASE_CLOSED,
        ]
        for event in events:
            metadata = event.event_metadata or {}
            assert metadata.get("backfill_source") == "backfill_intervention_events"
            assert "notes" not in metadata
            assert "summary" not in metadata
            assert "Legacy case" not in str(metadata)
            assert "Private contact notes" not in str(metadata)

        created = next(
            event for event in events if event.event_type is InterventionEventType.CASE_CREATED
        )
        assert created.event_metadata["student_id"] == case.student_id
        assert created.event_metadata["status"] == case.status.value
        assert created.event_metadata["owner_user_id"] == case.owner_user_id

        second = backfill_intervention_events(db, tenant_key="sunrise")
        assert second.total == 0
        assert (
            len(
                db.scalars(
                    select(InterventionEvent).where(
                        InterventionEvent.intervention_case_id == case.id
                    )
                ).all()
            )
            == len(events)
        )

    def test_backfill_respects_tenant_scope(self, db):
        sunrise_case = self._legacy_case(db, tenant_key="sunrise", with_contact=False)
        horizon_case = self._legacy_case(db, tenant_key="horizon", with_contact=False)

        stats = backfill_intervention_events(db, tenant_key="sunrise")
        assert stats.case_created == 1
        assert stats.total == 1

        sunrise_events = db.scalars(
            select(InterventionEvent).where(
                InterventionEvent.intervention_case_id == sunrise_case.id
            )
        ).all()
        horizon_events = db.scalars(
            select(InterventionEvent).where(
                InterventionEvent.intervention_case_id == horizon_case.id
            )
        ).all()
        assert len(sunrise_events) == 1
        assert horizon_events == []


def _create_case(admin_client, db, *, tenant_key: str = "sunrise") -> int:
    student = _at_risk_student_without_case(db, tenant_key=tenant_key)
    response = admin_client.post(
        "/api/interventions",
        json={"student_id": student.id, "signal_source": "at_risk_analytics"},
    )
    assert response.status_code == 200
    return response.json()["id"]


def _ensure_case_for_student(db, student: Student, *, created_by: User | None = None) -> int:
    existing = db.scalars(
        select(InterventionCase.id).where(
            InterventionCase.tenant_id == student.tenant_id,
            InterventionCase.student_id == student.id,
            InterventionCase.status.in_(
                (InterventionStatus.OPEN, InterventionStatus.IN_PROGRESS)
            ),
        )
    ).first()
    if existing is not None:
        return existing
    case = InterventionCase(
        tenant_id=student.tenant_id,
        student_id=student.id,
        signal_source="test_fixture",
        reason_summary="Test intervention case",
        priority=InterventionPriority.MEDIUM,
        status=InterventionStatus.OPEN,
        created_by_user_id=created_by.id if created_by else None,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case.id


class TestActionTaskCrud:
    def test_create_list_update_and_complete(self, admin_client, db, interventions_enabled):
        case_id = _create_case(admin_client, db)
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        teacher = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.TEACHER)
        ).first()
        assert teacher is not None

        created = admin_client.post(
            f"/api/interventions/{case_id}/tasks",
            json={
                "title": "Schedule parent meeting",
                "priority": "high",
                "assigned_to_user_id": teacher.id,
            },
        )
        assert created.status_code == 200
        task_id = created.json()["id"]
        assert created.json()["status"] == "open"
        assert created.json()["assigned_to_user_id"] == teacher.id

        listing = admin_client.get(f"/api/interventions/{case_id}/tasks")
        assert listing.status_code == 200
        assert listing.json()["count"] == 1

        detail = admin_client.get(f"/api/interventions/{case_id}")
        assert detail.status_code == 200
        assert len(detail.json()["tasks"]) == 1

        updated = admin_client.patch(
            f"/api/interventions/{case_id}/tasks/{task_id}",
            json={"status": "in_progress"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "in_progress"

        completed = admin_client.post(
            f"/api/interventions/{case_id}/tasks/{task_id}/complete",
            json={"completion_note": "Meeting held on Tuesday"},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert completed.json()["completion_note"] == "Meeting held on Tuesday"
        assert completed.json()["completed_at"] is not None

        repeat = admin_client.post(
            f"/api/interventions/{case_id}/tasks/{task_id}/complete",
            json={},
        )
        assert repeat.status_code == 422

    def test_reopen_completed_task_rejected(self, admin_client, db, interventions_enabled):
        case_id = _create_case(admin_client, db)
        created = admin_client.post(
            f"/api/interventions/{case_id}/tasks",
            json={"title": "Follow up call"},
        )
        task_id = created.json()["id"]
        admin_client.post(
            f"/api/interventions/{case_id}/tasks/{task_id}/complete",
            json={},
        )
        response = admin_client.patch(
            f"/api/interventions/{case_id}/tasks/{task_id}",
            json={"status": "open"},
        )
        assert response.status_code == 422


class TestActionTaskEvents:
    def test_task_created_and_completed_events_emitted(
        self, admin_client, db, interventions_enabled
    ):
        case_id = _create_case(admin_client, db)
        created = admin_client.post(
            f"/api/interventions/{case_id}/tasks",
            json={"title": "Review attendance plan"},
        )
        task_id = created.json()["id"]
        admin_client.post(
            f"/api/interventions/{case_id}/tasks/{task_id}/complete",
            json={},
        )

        timeline = admin_client.get(f"/api/interventions/{case_id}/events").json()
        types = [row["event_type"] for row in timeline["events"]]
        assert "task_created" in types
        assert "task_completed" in types

        created_event = next(
            row for row in timeline["events"] if row["event_type"] == "task_created"
        )
        assert created_event["metadata"]["task_id"] == task_id
        assert "completion_note" not in (created_event.get("metadata") or {})

        completed_event = next(
            row for row in timeline["events"] if row["event_type"] == "task_completed"
        )
        assert completed_event["metadata"]["task_id"] == task_id
        assert completed_event["metadata"]["to_status"] == "completed"
        assert "Meeting held" not in str(completed_event.get("metadata") or {})


class TestActionTaskScope:
    def test_teacher_can_manage_tasks_for_own_student(
        self, teacher_context, db, interventions_enabled
    ):
        client = teacher_context["client"]
        student_id = teacher_context["own_student_id"]
        student = db.get(Student, student_id)
        from app.deps import AccessScope
        from app.models import AcademicYear, Teacher

        teacher = db.get(Teacher, teacher_context["teacher_id"])
        year = db.scalars(
            select(AcademicYear).where(AcademicYear.tenant_id == student.tenant_id).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=student.tenant_id,
            user=db.get(User, teacher.user_id),
            academic_year=year,
            student_ids=frozenset({student_id}),
            section_ids=frozenset({student.section_id}),
            subject_ids=None,
            teacher=teacher,
            students=(student,),
        )
        standing = next(
            s
            for s in queries.student_standings(
                db,
                FilterParams(academic_year_id=year.id, section_id=student.section_id),
                scope,
            )
            if s.student_id == student_id
        )
        if not standing.risk.is_at_risk:
            pytest.skip("assigned student is not at-risk in seed")

        case_id = _ensure_case_for_student(db, student, created_by=scope.user)
        create_task = client.post(
            f"/api/interventions/{case_id}/tasks",
            json={"title": "Check homework completion"},
        )
        assert create_task.status_code == 200

    def test_teacher_cannot_access_tasks_for_outside_case(
        self, admin_client, teacher_context, db, interventions_enabled
    ):
        outside_id = teacher_context.get("outside_student_id")
        if outside_id is None:
            pytest.skip("no outside student")
        outside_student = db.get(Student, outside_id)
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN)
        ).one()
        case_id = _ensure_case_for_student(db, outside_student, created_by=admin)
        response = teacher_context["client"].get(f"/api/interventions/{case_id}/tasks")
        assert response.status_code == 404

    def test_assignee_only_visibility_hides_from_unassigned_teacher(
        self, admin_client, teacher_context, db, interventions_enabled
    ):
        client = teacher_context["client"]
        student_id = teacher_context["own_student_id"]
        student = db.get(Student, student_id)
        from app.deps import AccessScope
        from app.models import AcademicYear, Teacher

        teacher = db.get(Teacher, teacher_context["teacher_id"])
        year = db.scalars(
            select(AcademicYear).where(AcademicYear.tenant_id == student.tenant_id).limit(1)
        ).one()
        scope = AccessScope(
            tenant_id=student.tenant_id,
            user=db.get(User, teacher.user_id),
            academic_year=year,
            student_ids=frozenset({student_id}),
            section_ids=frozenset({student.section_id}),
            subject_ids=None,
            teacher=teacher,
            students=(student,),
        )
        standing = next(
            s
            for s in queries.student_standings(
                db,
                FilterParams(academic_year_id=year.id, section_id=student.section_id),
                scope,
            )
            if s.student_id == student_id
        )
        if not standing.risk.is_at_risk:
            pytest.skip("assigned student is not at-risk in seed")

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(User.tenant_id == tenant.id, User.role == Role.ADMIN)
        ).one()
        case = InterventionCase(
            tenant_id=student.tenant_id,
            student_id=student.id,
            signal_source="visibility_test",
            reason_summary="Visibility isolation test case",
            priority=InterventionPriority.LOW,
            status=InterventionStatus.OPEN,
            created_by_user_id=admin.id,
        )
        db.add(case)
        db.commit()
        db.refresh(case)
        case_id = case.id

        created = admin_client.post(
            f"/api/interventions/{case_id}/tasks",
            json={
                "title": "Private follow-up",
                "visibility": "assignee_only",
                "assigned_to_user_id": admin.id,
            },
        )
        assert created.status_code == 200
        private_task_id = created.json()["id"]

        teacher_listing = client.get(f"/api/interventions/{case_id}/tasks")
        assert teacher_listing.status_code == 200
        teacher_task_ids = [row["id"] for row in teacher_listing.json()["tasks"]]
        assert private_task_id not in teacher_task_ids

        admin_listing = admin_client.get(f"/api/interventions/{case_id}/tasks")
        assert admin_listing.status_code == 200
        assert private_task_id in [row["id"] for row in admin_listing.json()["tasks"]]


class TestActionTaskTenantIsolation:
    def test_horizon_admin_cannot_read_sunrise_task(
        self, admin_client, db, interventions_enabled
    ):
        from fastapi.testclient import TestClient

        from app.main import app

        case_id = _create_case(admin_client, db, tenant_key="sunrise")
        created = admin_client.post(
            f"/api/interventions/{case_id}/tasks",
            json={"title": "Sunrise-only task"},
        )
        task_id = created.json()["id"]

        horizon = db.scalars(select(Tenant).where(Tenant.key == "horizon")).one()
        email = db.scalars(
            select(User.email).where(
                User.tenant_id == horizon.id, User.role == Role.ADMIN
            )
        ).one()
        _enable_interventions(db, tenant_key="horizon")
        horizon_client = TestClient(app, headers={"Host": HORIZON_HOST}, follow_redirects=False)
        login = horizon_client.post(
            "/login",
            data={"email": email, "password": DEMO_PASSWORD},
        )
        assert login.status_code == 303
        response = horizon_client.get(f"/api/interventions/{case_id}/tasks")
        assert response.status_code == 404

        stored = db.get(ActionTask, task_id)
        assert stored is not None
        assert stored.tenant_id != horizon.id


class TestInterventionNav:
    def test_portal_nav_respects_feature_flag(self, db):
        from app.models import Role
        from app.portals import portal_nav
        from app.tenant.settings import TenantSettings

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        _disable_interventions(db)
        db.refresh(tenant)
        disabled = portal_nav(Role.ADMIN, TenantSettings.from_json(tenant.settings_json))
        disabled_hrefs = [item["href"] for group in disabled for item in group["items"]]
        assert "/admin/interventions" not in disabled_hrefs

        _enable_interventions(db)
        db.refresh(tenant)
        enabled = portal_nav(Role.ADMIN, TenantSettings.from_json(tenant.settings_json))
        enabled_hrefs = [item["href"] for group in enabled for item in group["items"]]
        assert "/admin/interventions" in enabled_hrefs

    def test_nav_shows_when_enabled(self, admin_client, db, interventions_enabled):
        response = admin_client.get("/admin/interventions")
        assert response.status_code == 200
        assert "Intervention queue" in response.text
        assert "/admin/interventions" in response.text
