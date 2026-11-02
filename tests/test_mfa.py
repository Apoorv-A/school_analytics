"""TOTP MFA enrolment, login, recovery codes, and verification."""

from __future__ import annotations

import copy

import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.audit import SecurityAuditAction
from app.db import SessionLocal
from app.main import app
from app.mfa import encrypt_secret, generate_totp_secret, hash_recovery_code
from app.models import Role, SecurityAuditEvent, Tenant, User, UserMfaRecoveryCode
from app.security import create_session_token
from seed.generate import DEMO_PASSWORD, SUNRISE_HOST
from tests.conftest import CsrfTestClient

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


@pytest.fixture
def mfa_policy(db):
    """Enable admin MFA for one test and restore tenant settings afterward."""
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    original_settings = copy.deepcopy(tenant.settings_json)
    settings = dict(tenant.settings_json or {})
    settings["auth"] = {
        **settings.get("auth", {}),
        "mfaEnabled": True,
        "mfaRequiredRoles": ["admin"],
    }
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()
    yield tenant
    tenant.settings_json = original_settings
    flag_modified(tenant, "settings_json")
    db.commit()


def _admin_user(db, tenant: Tenant | None = None) -> User:
    if tenant is None:
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    email = db.scalars(
        select(User.email)
        .where(User.role == Role.ADMIN, User.tenant_id == tenant.id)
        .limit(1)
    ).one()
    return db.scalars(
        select(User).where(User.email == email, User.tenant_id == tenant.id)
    ).one()


def _enroll_user(db, user: User, secret: str | None = None) -> str:
    raw = secret or generate_totp_secret()
    user.totp_secret_enc = encrypt_secret(raw)
    user.totp_pending_secret_enc = None
    db.commit()
    return raw


def _mfa_session_client(db, tenant: Tenant | None = None) -> tuple[CsrfTestClient, User, str]:
    user = _admin_user(db, tenant)
    secret = _enroll_user(db, user)
    client = CsrfTestClient(app, headers=DEFAULT_HEADERS, follow_redirects=False)
    login = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEMO_PASSWORD},
    )
    assert login.status_code == 200, login.text
    verify = client.post(
        "/api/auth/mfa/verify",
        json={"code": pyotp.TOTP(secret).now(), "mfaToken": login.json()["mfaToken"]},
    )
    assert verify.status_code == 200, verify.text
    return client, user, secret


class TestMfaEnrolment:
    def test_enroll_start_and_confirm_with_session(self, admin_client, db):
        user = _admin_user(db)
        user.totp_secret_enc = None
        db.commit()

        start = admin_client.post("/api/auth/mfa/enroll/start")
        assert start.status_code == 200
        body = start.json()
        assert "provisioningUri" in body
        assert body["secret"]

        code = pyotp.TOTP(body["secret"]).now()
        confirm = admin_client.post(
            "/api/auth/mfa/enroll/confirm",
            json={"code": code},
        )
        assert confirm.status_code == 200
        db.refresh(user)
        assert user.totp_secret_enc is not None
        assert user.mfa_enrolled_at is not None

    def test_enroll_confirm_rejects_bad_code(self, admin_client):
        admin_client.post("/api/auth/mfa/enroll/start")
        response = admin_client.post(
            "/api/auth/mfa/enroll/confirm",
            json={"code": "000000"},
        )
        assert response.status_code == 401


class TestMfaLogin:
    def test_login_without_mfa_returns_session(self, anon_client, db):
        user = _admin_user(db)
        response = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["mfaPending"] is False
        assert body["mfaEnrolmentNeeded"] is False
        assert anon_client.get("/api/charts/school.kpis").status_code == 200

    def test_login_with_mfa_pending_requires_verify(self, anon_client, db, mfa_policy):
        user = _admin_user(db, mfa_policy)
        secret = _enroll_user(db, user)

        login = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        assert login.status_code == 200
        body = login.json()
        assert body["mfaPending"] is True
        assert body["mfaEnrolmentNeeded"] is False
        assert "mfaToken" in body
        assert anon_client.get("/api/charts/school.kpis").status_code == 401

        verify = anon_client.post(
            "/api/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now(), "mfaToken": body["mfaToken"]},
        )
        assert verify.status_code == 200
        assert verify.json()["mfaPending"] is False
        assert anon_client.get("/api/charts/school.kpis").status_code == 200

    def test_mfa_verify_rejects_invalid_code(self, anon_client, db, mfa_policy):
        user = _admin_user(db, mfa_policy)
        _enroll_user(db, user)

        login = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        token = login.json()["mfaToken"]
        response = anon_client.post(
            "/api/auth/mfa/verify",
            json={"code": "000000", "mfaToken": token},
        )
        assert response.status_code == 401

    def test_enrolment_needed_when_role_requires_mfa(self, anon_client, db, mfa_policy):
        user = _admin_user(db, mfa_policy)
        user.totp_secret_enc = None
        db.commit()

        login = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        assert login.status_code == 403
        body = login.json()
        assert body["mfaEnrolmentNeeded"] is True
        assert body["mfaPending"] is False
        assert "enrolmentToken" in body

        start = anon_client.post(
            "/api/auth/mfa/enroll/start",
            json={"enrolmentToken": body["enrolmentToken"]},
        )
        assert start.status_code == 200
        secret = start.json()["secret"]

        confirm = anon_client.post(
            "/api/auth/mfa/enroll/confirm",
            json={
                "code": pyotp.TOTP(secret).now(),
                "enrolmentToken": body["enrolmentToken"],
            },
        )
        assert confirm.status_code == 200
        assert anon_client.get("/api/charts/school.kpis").status_code == 200

    def test_teacher_not_required_when_only_admin_in_policy(self, anon_client, db, mfa_policy):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        teacher_email = db.scalars(
            select(User.email)
            .where(User.role == Role.TEACHER, User.tenant_id == tenant.id)
            .limit(1)
        ).one()

        response = anon_client.post(
            "/api/auth/login",
            json={"email": teacher_email, "password": DEMO_PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["mfaPending"] is False

    def test_form_login_mfa_step(self, anon_client, db, mfa_policy):
        user = _admin_user(db, mfa_policy)
        secret = _enroll_user(db, user)

        password_step = anon_client.post(
            "/login",
            data={"email": user.email, "password": DEMO_PASSWORD},
        )
        assert password_step.status_code == 200
        assert "Authentication code" in password_step.text

        verify_step = anon_client.post(
            "/login",
            data={"mfa_code": pyotp.TOTP(secret).now()},
        )
        assert verify_step.status_code == 303
        assert verify_step.headers["location"] == "/admin"


class TestMfaRecoveryCodes:
    def test_generate_requires_reauthentication(self, db, mfa_policy):
        client, user, _secret = _mfa_session_client(db, mfa_policy)

        denied = client.post("/api/auth/mfa/recovery-codes", json={})
        assert denied.status_code == 422

        bad = client.post(
            "/api/auth/mfa/recovery-codes",
            json={"password": "wrong-password"},
        )
        assert bad.status_code == 401

        ok = client.post(
            "/api/auth/mfa/recovery-codes",
            json={"password": DEMO_PASSWORD},
        )
        assert ok.status_code == 200
        codes = ok.json()["recoveryCodes"]
        assert len(codes) == 10
        assert all("-" in code for code in codes)

        with SessionLocal() as session:
            stored = session.scalars(
                select(UserMfaRecoveryCode).where(UserMfaRecoveryCode.user_id == user.id)
            ).all()
            assert len(stored) == 10
            assert all(row.used_at is None for row in stored)
            for code in codes:
                assert hash_recovery_code(code) in {row.code_hash for row in stored}

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == user.tenant_id,
                    SecurityAuditEvent.action
                    == SecurityAuditAction.MFA_RECOVERY_CODES_GENERATED,
                    SecurityAuditEvent.target_id == str(user.id),
                )
            ).first()
            assert event is not None
            assert event.event_metadata == {"count": 10}

    def test_recovery_code_login_and_single_use(self, anon_client, db, mfa_policy):
        client, user, _secret = _mfa_session_client(db, mfa_policy)
        generated = client.post(
            "/api/auth/mfa/recovery-codes",
            json={"password": DEMO_PASSWORD},
        )
        assert generated.status_code == 200
        recovery_code = generated.json()["recoveryCodes"][0]

        login = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        token = login.json()["mfaToken"]
        verify = anon_client.post(
            "/api/auth/mfa/recovery",
            json={"recoveryCode": recovery_code, "mfaToken": token},
        )
        assert verify.status_code == 200
        assert "sa_session" in verify.cookies
        anon_client.cookies.update(verify.cookies)
        assert anon_client.get("/api/charts/school.kpis").status_code == 200

        login_again = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        reuse = anon_client.post(
            "/api/auth/mfa/recovery",
            json={
                "recoveryCode": recovery_code,
                "mfaToken": login_again.json()["mfaToken"],
            },
        )
        assert reuse.status_code == 401

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == user.tenant_id,
                    SecurityAuditEvent.action
                    == SecurityAuditAction.MFA_RECOVERY_CODE_USED,
                    SecurityAuditEvent.target_id == str(user.id),
                )
            ).first()
            assert event is not None
            assert event.event_metadata is None

    def test_form_recovery_login(self, anon_client, db, mfa_policy):
        client, user, _secret = _mfa_session_client(db, mfa_policy)
        codes = client.post(
            "/api/auth/mfa/recovery-codes",
            json={"password": DEMO_PASSWORD},
        ).json()["recoveryCodes"]

        anon_client.post("/login", data={"email": user.email, "password": DEMO_PASSWORD})
        response = anon_client.post(
            "/login/mfa/recovery",
            data={"recovery_code": codes[1]},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin"

    def test_admin_reset_clears_mfa_and_revokes_sessions(
        self, anon_client, db, mfa_policy
    ):
        admin_client, user, secret = _mfa_session_client(db, mfa_policy)
        old_version = user.session_version

        login = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        verify = anon_client.post(
            "/api/auth/mfa/verify",
            json={"code": pyotp.TOTP(secret).now(), "mfaToken": login.json()["mfaToken"]},
        )
        assert verify.status_code == 200
        anon_client.cookies.update(verify.cookies)
        assert anon_client.get("/api/charts/school.kpis").status_code == 200

        reset = admin_client.post(f"/api/admin/users/{user.id}/mfa/reset")
        assert reset.status_code == 200
        body = reset.json()
        assert body["mfaReset"] is True
        assert body["sessionVersion"] == old_version + 1

        db.refresh(user)
        assert user.totp_secret_enc is None
        assert user.mfa_enrolled_at is None
        assert user.session_version == old_version + 1
        assert (
            db.scalars(
                select(UserMfaRecoveryCode).where(UserMfaRecoveryCode.user_id == user.id)
            ).all()
            == []
        )

        stale = create_session_token(
            user.id, user.role.value, str(user.tenant_id), old_version
        )
        anon_client.cookies.set("sa_session", stale)
        assert anon_client.get("/api/charts/school.kpis").status_code == 401

        with SessionLocal() as session:
            reset_event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == user.tenant_id,
                    SecurityAuditEvent.action == SecurityAuditAction.MFA_RESET,
                    SecurityAuditEvent.target_id == str(user.id),
                )
            ).first()
            assert reset_event is not None

    def test_enrollment_records_audit_event(self, anon_client, db, mfa_policy):
        user = _admin_user(db, mfa_policy)
        user.totp_secret_enc = None
        db.commit()

        login = anon_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": DEMO_PASSWORD},
        )
        token = login.json()["enrolmentToken"]
        start = anon_client.post(
            "/api/auth/mfa/enroll/start",
            json={"enrolmentToken": token},
        )
        secret = start.json()["secret"]
        anon_client.post(
            "/api/auth/mfa/enroll/confirm",
            json={"code": pyotp.TOTP(secret).now(), "enrolmentToken": token},
        )

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == user.tenant_id,
                    SecurityAuditEvent.action == SecurityAuditAction.MFA_ENROLLED,
                    SecurityAuditEvent.target_id == str(user.id),
                )
            ).first()
            assert event is not None


class TestMfaConfigValidation:
    def test_invalid_role_rejected(self):
        from tenant_config.models import TenantConfigDocument

        with pytest.raises(ValueError, match="invalid mfaRequiredRoles"):
            TenantConfigDocument.model_validate(
                {
                    "tenant": {
                        "key": "x",
                        "displayName": "X",
                        "hostname": "x.localhost",
                        "environment": "dev",
                    },
                    "school": {"name": "X"},
                    "academics": {
                        "passPercentage": 33,
                        "atRiskPercentage": 45,
                        "atRiskAttendance": 75,
                    },
                    "auth": {"mfaEnabled": True, "mfaRequiredRoles": ["superuser"]},
                    "image": {"tag": "1.0.0"},
                }
            )
