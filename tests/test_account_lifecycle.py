"""Password reset, session revocation, and forced password change."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.account_lifecycle import (
    issue_password_reset_token,
    set_user_password,
)
from app.models import PasswordResetToken, Role, Tenant, User
from app.security import (
    create_session_token,
    hash_password,
    read_session_token,
    verify_password,
)
from seed.generate import DEMO_PASSWORD, SUNRISE_HOST

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


@pytest.fixture(autouse=True)
def _restore_seed_passwords(db):
    """Keep shared seed accounts usable for downstream tests in the suite."""
    yield
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    for role in (Role.TEACHER, Role.STUDENT, Role.PARENT):
        user = db.scalars(
            select(User)
            .where(User.role == role, User.tenant_id == tenant.id)
            .order_by(User.id)
            .limit(1)
        ).first()
        if user is not None:
            user.password_hash = hash_password(DEMO_PASSWORD)
            user.must_change_password = False
    db.commit()


def _teacher_user(db) -> User:
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    user = db.scalars(
        select(User)
        .where(User.role == Role.TEACHER, User.tenant_id == tenant.id)
        .limit(1)
    ).one()
    user.password_hash = hash_password(DEMO_PASSWORD)
    user.must_change_password = False
    db.commit()
    return user


class TestSessionRevocation:
    def test_password_change_invalidates_old_session_cookie(self, anon_client, db):
        from app.config import settings

        user = _teacher_user(db)
        login = anon_client.post(
            "/login", data={"email": user.email, "password": DEMO_PASSWORD}
        )
        assert login.status_code == 303
        client = anon_client
        old_version = user.session_version
        stale_cookie = client.cookies.get(settings.session_cookie_name)

        response = client.post(
            "/change-password",
            data={
                "current_password": DEMO_PASSWORD,
                "password": "NewSecure1!",
                "confirm_password": "NewSecure1!",
            },
        )
        assert response.status_code == 303

        db.refresh(user)
        assert user.session_version == old_version + 1

        client.cookies.set(settings.session_cookie_name, stale_cookie)
        blocked = client.get("/teacher")
        assert blocked.status_code == 303
        assert blocked.headers["location"] == "/login"

    def test_session_token_rejects_stale_version(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        user = db.scalars(
            select(User).where(User.role == Role.ADMIN, User.tenant_id == tenant.id)
        ).first()
        stale = create_session_token(
            user.id, user.role.value, str(user.tenant_id), user.session_version
        )
        user.session_version += 1
        db.commit()
        payload = read_session_token(stale)
        assert payload is not None
        assert payload["sv"] != user.session_version


class TestPasswordReset:
    def test_forgot_password_is_generic_for_unknown_email(self, anon_client):
        response = anon_client.post(
            "/forgot-password",
            data={"email": "nobody@sunrise.edu"},
        )
        assert response.status_code == 200
        assert "if an account exists" in response.text.lower()

    def test_admin_reset_returns_single_use_token(self, admin_client, anon_client, db):
        user = _teacher_user(db)
        response = admin_client.post(f"/api/admin/users/{user.id}/password-reset")
        assert response.status_code == 200
        body = response.json()
        assert body["resetToken"]
        assert "reset-password" in body["resetPath"]

        page = anon_client.get(f"/reset-password?token={body['resetToken']}")
        assert page.status_code == 200
        assert "Set new password" in page.text

        reset = anon_client.post(
            "/reset-password",
            data={
                "token": body["resetToken"],
                "password": "ResetPass9!",
                "confirm_password": "ResetPass9!",
            },
        )
        assert reset.status_code == 303
        assert reset.headers["location"] == "/login"

        db.refresh(user)
        assert verify_password("ResetPass9!", user.password_hash)

        reuse = anon_client.post(
            "/reset-password",
            data={
                "token": body["resetToken"],
                "password": "AnotherPass9!",
                "confirm_password": "AnotherPass9!",
            },
        )
        assert reuse.status_code == 400

    def test_new_reset_revokes_previous_token(self, admin_client, db):
        user = _teacher_user(db)
        first = admin_client.post(f"/api/admin/users/{user.id}/password-reset").json()
        second = admin_client.post(f"/api/admin/users/{user.id}/password-reset").json()

        page = admin_client.get(f"/reset-password?token={first['resetToken']}")
        assert page.status_code == 400

        page2 = admin_client.get(f"/reset-password?token={second['resetToken']}")
        assert page2.status_code == 200


class TestForcedPasswordChange:
    def test_must_change_password_redirects_after_login(self, db, anon_client):
        user = _teacher_user(db)
        email = user.email
        user.must_change_password = True
        db.commit()

        login = anon_client.post(
            "/login", data={"email": email, "password": DEMO_PASSWORD}
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/change-password"

        blocked = anon_client.get("/teacher")
        assert blocked.status_code == 303
        assert blocked.headers["location"] == "/change-password"

        changed = anon_client.post(
            "/change-password",
            data={
                "current_password": DEMO_PASSWORD,
                "password": "ForcedNew9!",
                "confirm_password": "ForcedNew9!",
            },
        )
        assert changed.status_code == 303
        assert changed.headers["location"] == "/teacher"

        db.refresh(user)
        assert user.must_change_password is False

    def test_api_blocked_until_password_changed(self, db, anon_client):
        user = _teacher_user(db)
        email = user.email
        user.must_change_password = True
        db.commit()

        anon_client.post("/login", data={"email": email, "password": DEMO_PASSWORD})
        api = anon_client.get("/api/filters")
        assert api.status_code == 403
        assert api.json()["detail"] == "Password change required."


class TestAdminInvite:
    def test_admin_invite_creates_user_with_must_change_password(
        self, admin_client, anon_client, db
    ):
        response = admin_client.post(
            "/api/admin/users/invite",
            json={
                "email": "invited.teacher@sunrise.edu",
                "full_name": "Invited Teacher",
                "role": "teacher",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["mustChangePassword"] is True
        assert body["tempPassword"]
        assert body["activationToken"]
        assert "reset-password" in body["activationPath"]

        user = db.scalars(
            select(User).where(User.email == "invited.teacher@sunrise.edu")
        ).one()
        assert user.must_change_password is True
        assert verify_password(body["tempPassword"], user.password_hash)

        page = anon_client.get(f"/reset-password?token={body['activationToken']}")
        assert page.status_code == 200

    def test_admin_invite_rejects_duplicate_email(self, admin_client, db):
        user = _teacher_user(db)
        response = admin_client.post(
            "/api/admin/users/invite",
            json={
                "email": user.email,
                "full_name": "Duplicate",
                "role": "teacher",
            },
        )
        assert response.status_code == 409

    def test_admin_invite_rejects_admin_role(self, admin_client):
        response = admin_client.post(
            "/api/admin/users/invite",
            json={
                "email": "new.admin@sunrise.edu",
                "full_name": "New Admin",
                "role": "admin",
            },
        )
        assert response.status_code == 422


class TestPasswordResetTokenModel:
    def test_issue_token_creates_hashed_record(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        user = db.scalars(
            select(User).where(User.role == Role.PARENT, User.tenant_id == tenant.id)
        ).first()
        raw = issue_password_reset_token(db, user)
        record = db.scalars(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        ).one()
        assert record.token_hash != raw
        assert record.used_at is None

    def test_set_user_password_revokes_tokens(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        user = db.scalars(
            select(User).where(User.role == Role.STUDENT, User.tenant_id == tenant.id)
        ).first()
        issue_password_reset_token(db, user)
        set_user_password(db, user, "StudentNew9!")
        record = db.scalars(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        ).one()
        assert record.used_at is not None
