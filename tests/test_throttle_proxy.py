"""Login throttle Redis backend, fail-closed policy, and trusted-proxy client IP."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit import SecurityAuditAction, SecurityAuditTarget
from app.db import SessionLocal
from app.main import app
from app.models import Role, SecurityAuditEvent, Tenant, User
from app.throttle import (
    LoginThrottleStoreUnavailable,
    RedisLoginThrottleStore,
    get_login_throttle_store,
    reset_login_throttle_store_for_tests,
    validate_production_login_throttle_config,
)
from seed.generate import DEMO_PASSWORD, SUNRISE_HOST


@pytest.fixture(autouse=True)
def _reset_throttle():
    reset_login_throttle_store_for_tests()
    yield
    reset_login_throttle_store_for_tests()


def _fake_redis_module(mock_client: MagicMock) -> MagicMock:
    module = MagicMock()
    module.Redis.from_url.return_value = mock_client
    return module


class TestRedisLoginThrottleStore:
    def test_record_failure_uses_redis_incr_and_expire(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.incr.return_value = 1

        with patch.dict(sys.modules, {"redis": _fake_redis_module(mock_client)}):
            store = RedisLoginThrottleStore("redis://localhost:6379/0")

        assert store.record_failure("test-key") is False
        mock_client.incr.assert_called_once()
        mock_client.expire.assert_called_once()

    def test_record_failure_returns_true_at_lockout_threshold(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.incr.return_value = 8

        with patch.dict(sys.modules, {"redis": _fake_redis_module(mock_client)}):
            store = RedisLoginThrottleStore("redis://localhost:6379/0")

        assert store.record_failure("test-key") is True

    def test_is_locked_out_when_count_reaches_limit(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.get.return_value = "8"

        with patch.dict(sys.modules, {"redis": _fake_redis_module(mock_client)}):
            store = RedisLoginThrottleStore("redis://localhost:6379/0")

        assert store.is_locked_out("test-key") is True

    def test_redis_unavailable_fail_closed(self):
        mock_client = MagicMock()
        mock_client.ping.side_effect = ConnectionError("refused")

        with patch.dict(sys.modules, {"redis": _fake_redis_module(mock_client)}):
            store = RedisLoginThrottleStore(
                "redis://localhost:6379/0",
                fail_closed=True,
            )

        with pytest.raises(LoginThrottleStoreUnavailable):
            store.record_failure("down-key")

    @staticmethod
    def _shared_redis_store() -> RedisLoginThrottleStore:
        from tests.fake_shared_redis import FakeRedis, FakeServer

        server = FakeServer()
        client = FakeRedis(server=server, decode_responses=True)
        return RedisLoginThrottleStore("redis://fake", client=client)

    def test_simulated_second_replica_shares_lockout_state(self):
        from tests.fake_shared_redis import FakeRedis, FakeServer

        server = FakeServer()
        replica_a = RedisLoginThrottleStore(
            "redis://fake",
            client=FakeRedis(server=server, decode_responses=True),
        )
        replica_b = RedisLoginThrottleStore(
            "redis://fake",
            client=FakeRedis(server=server, decode_responses=True),
        )
        key = "shared-login-key"
        for _ in range(7):
            assert replica_a.record_failure(key) is False
        assert replica_b.record_failure(key) is True
        assert replica_b.is_locked_out(key) is True
        assert replica_a.is_locked_out(key) is True

    def test_restart_simulation_does_not_reset_lockout(self):
        from tests.fake_shared_redis import FakeRedis, FakeServer

        server = FakeServer()
        first_pod = RedisLoginThrottleStore(
            "redis://fake",
            client=FakeRedis(server=server, decode_responses=True),
        )
        key = "restart-key"
        for _ in range(8):
            first_pod.record_failure(key)
        assert first_pod.is_locked_out(key) is True

        restarted_pod = RedisLoginThrottleStore(
            "redis://fake",
            client=FakeRedis(server=server, decode_responses=True),
        )
        assert restarted_pod.is_locked_out(key) is True

    @pytest.mark.skipif(
        __import__("importlib").util.find_spec("redis") is None,
        reason="redis package not installed",
    )
    def test_get_store_uses_redis_when_configured(self, monkeypatch):
        import app.throttle as throttle_module

        monkeypatch.setattr(throttle_module.settings, "login_throttle_backend", "redis")
        monkeypatch.setattr(
            throttle_module.settings,
            "login_throttle_redis_url",
            "redis://localhost:6379/0",
        )
        throttle_module._store = None

        mock_client = MagicMock()
        mock_client.ping.return_value = True
        with patch("redis.Redis.from_url", return_value=mock_client):
            store = get_login_throttle_store()
        assert isinstance(store, RedisLoginThrottleStore)

        throttle_module._store = None
        reset_login_throttle_store_for_tests()


class TestProductionLoginThrottleConfig:
    def test_production_requires_redis_backend(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.debug", False)
        monkeypatch.setattr("app.config.settings.login_throttle_backend", "memory")
        reset_login_throttle_store_for_tests()
        errors = validate_production_login_throttle_config()
        assert any("LOGIN_THROTTLE_BACKEND" in message for message in errors)

    def test_production_requires_redis_url(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.debug", False)
        monkeypatch.setattr("app.config.settings.login_throttle_backend", "redis")
        monkeypatch.setattr("app.config.settings.login_throttle_redis_url", "")
        reset_login_throttle_store_for_tests()
        errors = validate_production_login_throttle_config()
        assert any("LOGIN_THROTTLE_REDIS_URL" in message for message in errors)

    def test_debug_skips_production_requirements(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.debug", True)
        monkeypatch.setattr("app.config.settings.login_throttle_backend", "memory")
        assert validate_production_login_throttle_config() == []


class TestLoginThrottleHttp:
    def test_store_unavailable_returns_503_without_account_leak(self, anon_client, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        class _UnavailableStore:
            def is_locked_out(self, key: str) -> bool:
                raise LoginThrottleStoreUnavailable("down")

            def record_failure(self, key: str) -> bool:
                raise LoginThrottleStoreUnavailable("down")

            def clear_failures(self, key: str) -> None:
                raise LoginThrottleStoreUnavailable("down")

        reset_login_throttle_store_for_tests(_UnavailableStore())
        response = anon_client.post(
            "/login",
            data={"email": admin.email, "password": "wrong-password"},
            headers={"X-Correlation-ID": "throttle-down-1"},
        )
        assert response.status_code == 503
        assert "temporarily unavailable" in response.text.lower()
        assert admin.email not in response.text

        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.correlation_id == "throttle-down-1",
                )
            ).one()
            assert event.action == SecurityAuditAction.AUTH_THROTTLE_UNAVAILABLE

    def test_lockout_records_audit_event(self, anon_client, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        for attempt in range(8):
            anon_client.post(
                "/login",
                data={"email": admin.email, "password": "wrong-password"},
                headers={"X-Correlation-ID": f"lockout-{attempt}"},
            )

        with SessionLocal() as session:
            lockout = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.LOGIN_LOCKOUT,
                )
            ).all()
            assert lockout

    def test_account_lockout_blocks_other_ips_for_same_email(self, db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "trusted_proxy", True)

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        email = db.scalars(
            select(User.email).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        client_a = TestClient(
            app,
            headers={"Host": SUNRISE_HOST, "X-Forwarded-For": "198.51.100.10"},
            follow_redirects=False,
        )
        client_b = TestClient(
            app,
            headers={"Host": SUNRISE_HOST, "X-Forwarded-For": "198.51.100.99"},
            follow_redirects=False,
        )

        for _ in range(8):
            client_a.post("/login", data={"email": email, "password": "wrong"})
        locked = client_b.post(
            "/login", data={"email": email, "password": DEMO_PASSWORD}
        )
        assert locked.status_code == 429

    def test_ip_lockout_blocks_other_emails_from_same_ip(self, db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "trusted_proxy", True)

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin_email = db.scalars(
            select(User.email).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()
        teacher_email = db.scalars(
            select(User.email).where(
                User.tenant_id == tenant.id, User.role == Role.TEACHER
            )
            .limit(1)
        ).one()

        shared_headers = {"Host": SUNRISE_HOST, "X-Forwarded-For": "203.0.113.50"}
        client = TestClient(app, headers=shared_headers, follow_redirects=False)

        for _ in range(8):
            client.post(
                "/login",
                data={"email": admin_email, "password": "wrong"},
            )
        locked = client.post(
            "/login",
            data={"email": teacher_email, "password": DEMO_PASSWORD},
        )
        assert locked.status_code == 429

    def test_unknown_email_same_response_as_wrong_password(self, anon_client, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        wrong_password = anon_client.post(
            "/login",
            data={"email": admin.email, "password": "wrong-password"},
        )
        unknown_email = anon_client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrong-password"},
        )
        assert wrong_password.status_code == unknown_email.status_code == 401
        assert "Email or password is incorrect." in wrong_password.text
        assert wrong_password.text == unknown_email.text

        with SessionLocal() as session:
            unknown_event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.target_id == "unknown",
                    SecurityAuditEvent.action == SecurityAuditAction.LOGIN_FAILURE,
                )
            ).first()
            assert unknown_event is not None
            assert unknown_event.target_type == SecurityAuditTarget.USER


class TestTrustedProxy:
    def test_forwarded_for_ignored_when_trusted_proxy_disabled(self, db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "trusted_proxy", False)

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        email = db.scalars(
            select(User.email).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()

        client = TestClient(
            app,
            headers={"Host": SUNRISE_HOST},
            follow_redirects=False,
        )

        for _ in range(8):
            client.post(
                "/login",
                data={"email": email, "password": "wrong"},
                headers={"X-Forwarded-For": "203.0.113.10"},
            )
        locked = client.post(
            "/login",
            data={"email": email, "password": DEMO_PASSWORD},
            headers={"X-Forwarded-For": "203.0.113.99"},
        )
        # Spoofed X-Forwarded-For must not bypass lockout when proxy is not trusted.
        assert locked.status_code == 429

    def test_forwarded_for_honoured_when_trusted_proxy_enabled(self, db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "trusted_proxy", True)

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin_email = db.scalars(
            select(User.email).where(
                User.tenant_id == tenant.id, User.role == Role.ADMIN
            )
        ).one()
        teacher_email = db.scalars(
            select(User.email).where(
                User.tenant_id == tenant.id, User.role == Role.TEACHER
            )
            .limit(1)
        ).one()

        shared_ip_headers = {"Host": SUNRISE_HOST, "X-Forwarded-For": "198.51.100.44"}
        client = TestClient(
            app, headers=shared_ip_headers, follow_redirects=False
        )

        for _ in range(8):
            client.post("/login", data={"email": admin_email, "password": "wrong"})
        locked = client.post(
            "/login", data={"email": admin_email, "password": "wrong"}
        )
        assert locked.status_code == 429

        other_client = TestClient(
            app,
            headers={"Host": SUNRISE_HOST, "X-Forwarded-For": "198.51.100.99"},
            follow_redirects=False,
        )
        # Same account stays locked from a new IP; a different account on a fresh IP works.
        still_locked = other_client.post(
            "/login", data={"email": admin_email, "password": DEMO_PASSWORD}
        )
        assert still_locked.status_code == 429

        allowed = other_client.post(
            "/login", data={"email": teacher_email, "password": DEMO_PASSWORD}
        )
        assert allowed.status_code == 303
