"""Security audit event recording and tenant isolation."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.audit import SecurityAuditAction, SecurityAuditTarget, record_security_event
from app.db import SessionLocal
from app.models import Role, SecurityAuditEvent, Tenant, User
from app.throttle import reset_login_throttle_store_for_tests
from seed.generate import DEMO_PASSWORD


@pytest.fixture(autouse=True)
def _reset_throttle():
    reset_login_throttle_store_for_tests()
    yield
    reset_login_throttle_store_for_tests()


class TestSecurityAuditEvents:
    def test_failed_login_records_audit_event(self, anon_client, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        response = anon_client.post(
            "/login",
            data={"email": admin.email, "password": "wrong-password"},
            headers={"X-Correlation-ID": "test-login-fail-1"},
        )
        assert response.status_code == 401

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.LOGIN_FAILURE,
                    SecurityAuditEvent.correlation_id == "test-login-fail-1",
                )
            ).one()
            assert event.target_type == SecurityAuditTarget.USER
            assert event.target_id == str(admin.id)
            assert event.actor_user_id == admin.id
            assert event.event_metadata == {"reason": "invalid_credentials"}
            assert "password" not in (event.event_metadata or {})

    def test_successful_login_records_audit_event(self, anon_client, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        response = anon_client.post(
            "/login",
            data={"email": admin.email, "password": DEMO_PASSWORD},
            headers={"X-Correlation-ID": "test-login-ok-1"},
        )
        assert response.status_code == 303

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.LOGIN_SUCCESS,
                    SecurityAuditEvent.correlation_id == "test-login-ok-1",
                )
            ).one()
            assert event.actor_user_id == admin.id
            assert event.target_id == str(admin.id)

    def test_audit_events_are_tenant_isolated(self, db):
        sunrise = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        horizon = db.scalars(select(Tenant).where(Tenant.key == "horizon")).one()
        sunrise_admin = db.scalars(
            select(User).where(User.tenant_id == sunrise.id, User.role == Role.ADMIN)
        ).one()

        record_security_event(
            db,
            tenant_id=sunrise.id,
            action=SecurityAuditAction.LOGIN_SUCCESS,
            target_type=SecurityAuditTarget.USER,
            target_id=sunrise_admin.id,
            actor_user_id=sunrise_admin.id,
            correlation_id="sunrise-only",
        )
        db.commit()

        sunrise_events = db.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.tenant_id == sunrise.id,
                SecurityAuditEvent.correlation_id == "sunrise-only",
            )
        ).all()
        horizon_events = db.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.tenant_id == horizon.id,
                SecurityAuditEvent.correlation_id == "sunrise-only",
            )
        ).all()

        assert len(sunrise_events) == 1
        assert horizon_events == []

    def test_metadata_strips_sensitive_fields(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()

        record_security_event(
            db,
            tenant_id=tenant.id,
            action=SecurityAuditAction.LOGIN_FAILURE,
            target_type=SecurityAuditTarget.USER,
            target_id="unknown",
            metadata={
                "reason": "invalid_credentials",
                "password": "must-not-persist",
                "resetToken": "secret-value",
            },
            correlation_id="sanitize-test",
        )
        db.commit()

        event = db.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.correlation_id == "sanitize-test"
            )
        ).one()
        assert event.event_metadata == {"reason": "invalid_credentials"}


class TestDataAccessAuditEvents:
    def test_csv_export_records_audit_event(self, admin_client, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        settings = dict(tenant.settings_json or {})
        features = dict(settings.get("features") or {})
        features["exports"] = True
        settings["features"] = features
        tenant.settings_json = settings
        flag_modified(tenant, "settings_json")
        db.commit()

        response = admin_client.get(
            "/export/school.kpis.csv",
            headers={"X-Correlation-ID": "export-audit-1"},
        )
        assert response.status_code == 200

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.EXPORT_DOWNLOAD,
                    SecurityAuditEvent.correlation_id == "export-audit-1",
                )
            ).one()
            assert event.target_type == SecurityAuditTarget.EXPORT
            assert event.target_id == "school.kpis"

    def test_sensitive_chart_access_records_audit_event(self, admin_client, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        response = admin_client.get(
            "/api/charts/school.at_risk",
            headers={"X-Correlation-ID": "chart-audit-1"},
        )
        assert response.status_code == 200

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.CHART_ACCESS,
                    SecurityAuditEvent.correlation_id == "chart-audit-1",
                )
            ).one()
            assert event.target_type == SecurityAuditTarget.CHART
            assert event.target_id == "school.at_risk"

    def test_import_apply_records_audit_event(self, db, tmp_path):
        from app.import_.apply import apply_directory

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        structure = tmp_path / "school_structure.csv"
        structure.write_text(
            "grade_level,grade_name,section_name,subject_code,subject_name\n"
            "9,Grade 9,Z,ART,Art and Design\n",
            encoding="utf-8",
        )

        result = apply_directory(
            db,
            tenant.id,
            tmp_path,
            source="upload",
            user_id=admin.id,
        )
        assert result["applied"] is True

        event = db.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.tenant_id == tenant.id,
                SecurityAuditEvent.action == SecurityAuditAction.IMPORT_APPLY,
                SecurityAuditEvent.actor_user_id == admin.id,
            )
            .order_by(SecurityAuditEvent.id.desc())
        ).first()
        assert event is not None
        assert event.target_type == SecurityAuditTarget.IMPORT
        assert event.target_id == "bulk"
        assert event.event_metadata["source"] == "upload"
        assert "school_structure.csv" in event.event_metadata["files"]
