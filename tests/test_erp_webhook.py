"""ERP webhook HMAC signature and replay protection."""

from __future__ import annotations

import io
import time
import uuid
import zipfile

import pytest
from sqlalchemy import select

from app.audit import SecurityAuditAction, SecurityAuditTarget
from app.db import SessionLocal
from app.erp_webhook_security import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    RedisWebhookReplayStore,
    WebhookAuthFailure,
    WebhookReplayStoreUnavailable,
    compute_signature,
    reset_webhook_replay_store_for_tests,
    validate_production_webhook_replay_config,
    verify_webhook_request,
)
from app.models import SecurityAuditEvent, Tenant
from tests.conftest import DEFAULT_HEADERS
from tests.test_import_apply import _enable_bulk_import, _set_erp_webhook_secret


@pytest.fixture(autouse=True)
def _reset_replay_store():
    reset_webhook_replay_store_for_tests()
    yield
    reset_webhook_replay_store_for_tests()


def _build_multipart_body(
    *,
    field_name: str = "file",
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> tuple[bytes, str]:
    boundary = "----SchoolAnalyticsWebhookBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _signed_webhook_headers(
    *,
    signing_key: str,
    body: bytes,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    nonce_value = nonce or f"nonce-{uuid.uuid4().hex}"
    signature = compute_signature(signing_key, ts, nonce_value, body)
    return {
        HEADER_TIMESTAMP: ts,
        HEADER_NONCE: nonce_value,
        HEADER_SIGNATURE: signature,
    }


def _post_signed_webhook(
    client,
    *,
    signing_key: str,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    extra_headers: dict[str, str] | None = None,
    timestamp: int | None = None,
    nonce: str | None = None,
):
    body, multipart_type = _build_multipart_body(
        filename=filename,
        content=content,
        content_type=content_type,
    )
    headers = {
        **DEFAULT_HEADERS,
        "Content-Type": multipart_type,
        **_signed_webhook_headers(
            signing_key=signing_key,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
        ),
    }
    if extra_headers:
        headers.update(extra_headers)
    return client.post("/api/erp/import", content=body, headers=headers)


class TestWebhookSignatureVerification:
    def test_missing_signature_headers_returns_401_and_audit_event(
        self, anon_client, db, monkeypatch
    ):
        _enable_bulk_import(db)
        _set_erp_webhook_secret(db, "tenant-secret", monkeypatch=monkeypatch)

        response = anon_client.post(
            "/api/erp/import",
            files={"file": ("scores.csv", b"admission_no\n", "text/csv")},
        )
        assert response.status_code == 401

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.WEBHOOK_AUTH_FAILURE,
                )
            ).one()
            assert event.target_type == SecurityAuditTarget.WEBHOOK
            assert event.event_metadata == {"reason": "missing_headers"}

    def test_invalid_signature_returns_401_and_audit_event(
        self, anon_client, db, monkeypatch
    ):
        _enable_bulk_import(db)
        signing_key = "tenant-secret"
        _set_erp_webhook_secret(db, signing_key, monkeypatch=monkeypatch)
        body, multipart_type = _build_multipart_body(
            filename="scores.csv",
            content=b"admission_no\n",
            content_type="text/csv",
        )
        headers = {
            **DEFAULT_HEADERS,
            "Content-Type": multipart_type,
            **_signed_webhook_headers(signing_key=signing_key, body=body),
            HEADER_SIGNATURE: "v1=deadbeef",
            "X-Correlation-ID": "webhook-invalid-signature",
        }

        response = anon_client.post("/api/erp/import", content=body, headers=headers)
        assert response.status_code == 401

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        with SessionLocal() as session:
            event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.WEBHOOK_AUTH_FAILURE,
                    SecurityAuditEvent.correlation_id == "webhook-invalid-signature",
                )
            ).one()
            assert event.event_metadata == {"reason": "invalid_signature"}

    def test_expired_timestamp_returns_401(self, anon_client, db, monkeypatch):
        _enable_bulk_import(db)
        signing_key = "tenant-secret"
        _set_erp_webhook_secret(db, signing_key, monkeypatch=monkeypatch)
        response = _post_signed_webhook(
            anon_client,
            signing_key=signing_key,
            filename="scores.csv",
            content=b"admission_no\n",
            content_type="text/csv",
            timestamp=int(time.time()) - 600,
        )
        assert response.status_code == 401

    def test_future_timestamp_beyond_skew_returns_401(self, anon_client, db, monkeypatch):
        _enable_bulk_import(db)
        signing_key = "tenant-secret"
        _set_erp_webhook_secret(db, signing_key, monkeypatch=monkeypatch)
        response = _post_signed_webhook(
            anon_client,
            signing_key=signing_key,
            filename="scores.csv",
            content=b"admission_no\n",
            content_type="text/csv",
            timestamp=int(time.time()) + 600,
        )
        assert response.status_code == 401

    def test_replay_nonce_returns_403(self, anon_client, db, monkeypatch):
        _enable_bulk_import(db)
        signing_key = "tenant-secret"
        _set_erp_webhook_secret(db, signing_key, monkeypatch=monkeypatch)
        nonce = "fixed-replay-nonce"
        content = b"admission_no\n"

        first = _post_signed_webhook(
            anon_client,
            signing_key=signing_key,
            filename="scores.csv",
            content=content,
            content_type="text/csv",
            nonce=nonce,
        )
        assert first.status_code in {200, 400}

        second = _post_signed_webhook(
            anon_client,
            signing_key=signing_key,
            filename="scores.csv",
            content=content,
            content_type="text/csv",
            nonce=nonce,
        )
        assert second.status_code == 403

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        with SessionLocal() as session:
            replay_event = session.scalars(
                select(SecurityAuditEvent).where(
                    SecurityAuditEvent.tenant_id == tenant.id,
                    SecurityAuditEvent.action == SecurityAuditAction.WEBHOOK_AUTH_FAILURE,
                    SecurityAuditEvent.event_metadata == {"reason": "replay"},
                )
            ).one()
            assert replay_event.target_type == SecurityAuditTarget.WEBHOOK

    def test_valid_signature_applies_import(self, anon_client, db, monkeypatch):
        _enable_bulk_import(db)
        signing_key = "test-webhook-token"
        _set_erp_webhook_secret(db, signing_key, monkeypatch=monkeypatch)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "users.csv",
                "email,full_name,role\n"
                "webhook.user@example.com,Webhook User,teacher\n",
            )

        response = _post_signed_webhook(
            anon_client,
            signing_key=signing_key,
            filename="bundle.zip",
            content=buffer.getvalue(),
            content_type="application/zip",
        )
        assert response.status_code == 200
        assert response.json()["applied"] is True

    def test_signing_secret_ref_preferred_over_webhook_secret_ref(
        self, anon_client, db, monkeypatch
    ):
        from sqlalchemy.orm.attributes import flag_modified

        from app.models import Tenant
        from app.secrets import secret_env_name

        _enable_bulk_import(db)
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        original_settings = dict(tenant.settings_json or {})
        settings = dict(original_settings)
        settings["erp"] = {
            "webhookSecretRef": "sunrise/erp-webhook",
            "webhookSigningSecretRef": "sunrise/erp-signing",
        }
        tenant.settings_json = settings
        flag_modified(tenant, "settings_json")
        db.commit()
        monkeypatch.setenv(secret_env_name("sunrise/erp-webhook"), "legacy-token")
        monkeypatch.setenv(secret_env_name("sunrise/erp-signing"), "dedicated-signing-secret")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "users.csv",
                "email,full_name,role\n"
                "signed.user@example.com,Signed User,teacher\n",
            )

        try:
            wrong_secret = _post_signed_webhook(
                anon_client,
                signing_key="legacy-token",
                filename="bundle.zip",
                content=buffer.getvalue(),
                content_type="application/zip",
            )
            assert wrong_secret.status_code == 401

            right_secret = _post_signed_webhook(
                anon_client,
                signing_key="dedicated-signing-secret",
                filename="bundle.zip",
                content=buffer.getvalue(),
                content_type="application/zip",
            )
            assert right_secret.status_code == 200
            assert right_secret.json()["applied"] is True
        finally:
            tenant.settings_json = original_settings
            flag_modified(tenant, "settings_json")
            db.commit()

    def test_missing_resolved_secret_returns_401(self, anon_client, db, monkeypatch):
        from sqlalchemy.orm.attributes import flag_modified

        from app.models import Tenant
        from app.secrets import secret_env_name

        _enable_bulk_import(db)
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        original_settings = dict(tenant.settings_json or {})
        settings = dict(original_settings)
        settings["erp"] = {"webhookSecretRef": "sunrise/erp-webhook"}
        tenant.settings_json = settings
        flag_modified(tenant, "settings_json")
        db.commit()
        monkeypatch.delenv(secret_env_name("sunrise/erp-webhook"), raising=False)

        try:
            response = _post_signed_webhook(
                anon_client,
                signing_key="unused-secret",
                filename="scores.csv",
                content=b"admission_no\n",
                content_type="text/csv",
            )
            assert response.status_code == 401
        finally:
            tenant.settings_json = original_settings
            flag_modified(tenant, "settings_json")
            db.commit()


class TestWebhookSecurityHelpers:
    def test_verify_webhook_request_accepts_valid_signature(self):
        secret = "unit-test-secret"
        body = b"payload"
        timestamp = "1700000000"
        nonce = "nonce-12345678"
        signature = compute_signature(secret, timestamp, nonce, body)
        result = verify_webhook_request(
            tenant_key="sunrise",
            signing_secret=secret,
            timestamp_header=timestamp,
            nonce_header=nonce,
            signature_header=signature,
            body=body,
            now=1_700_000_000,
        )
        assert result.ok is True

    def test_verify_webhook_request_rejects_future_timestamp_beyond_skew(self):
        secret = "unit-test-secret"
        body = b"payload"
        timestamp = "1700000000"
        nonce = "nonce-12345678"
        signature = compute_signature(secret, timestamp, nonce, body)
        result = verify_webhook_request(
            tenant_key="sunrise",
            signing_secret=secret,
            timestamp_header=timestamp,
            nonce_header=nonce,
            signature_header=signature,
            body=body,
            now=1_700_000_000 - 600,
        )
        assert result.ok is False
        assert result.failure == WebhookAuthFailure.TIMESTAMP_EXPIRED


class TestRedisWebhookReplayStore:
    @staticmethod
    def _shared_redis_store() -> RedisWebhookReplayStore:
        from tests.fake_shared_redis import FakeRedis, FakeServer

        server = FakeServer()
        client = FakeRedis(server=server, decode_responses=True)
        return RedisWebhookReplayStore("redis://fake", client=client)

    def test_valid_first_request_reserves_nonce(self):
        store = self._shared_redis_store()
        assert store.reserve_nonce("sunrise", "nonce-first-1") is True

    def test_repeated_request_rejects_replay(self):
        store = self._shared_redis_store()
        nonce = "nonce-replay-1"
        assert store.reserve_nonce("sunrise", nonce) is True
        assert store.reserve_nonce("sunrise", nonce) is False

    def test_simulated_second_replica_shares_replay_state(self):
        from tests.fake_shared_redis import FakeRedis, FakeServer

        server = FakeServer()
        replica_a = RedisWebhookReplayStore(
            "redis://fake",
            client=FakeRedis(server=server, decode_responses=True),
        )
        replica_b = RedisWebhookReplayStore(
            "redis://fake",
            client=FakeRedis(server=server, decode_responses=True),
        )
        nonce = "nonce-shared-replica"
        assert replica_a.reserve_nonce("sunrise", nonce) is True
        assert replica_b.reserve_nonce("sunrise", nonce) is False

    def test_tenant_scoped_nonces_do_not_collide(self):
        store = self._shared_redis_store()
        nonce = "shared-nonce-value"
        assert store.reserve_nonce("sunrise", nonce) is True
        assert store.reserve_nonce("horizon", nonce) is True

    def test_redis_unavailable_fail_closed(self):
        class _BrokenRedis:
            def set(self, *args, **kwargs):
                raise ConnectionError("redis down")

            def ping(self):
                raise ConnectionError("redis down")

        store = RedisWebhookReplayStore(
            "redis://fake",
            client=_BrokenRedis(),
            fail_closed=True,
        )
        with pytest.raises(WebhookReplayStoreUnavailable):
            store.reserve_nonce("sunrise", "nonce-down-1")

    def test_verify_webhook_request_returns_503_when_store_unavailable(self):
        class _UnavailableStore:
            def reserve_nonce(self, tenant_key: str, nonce: str) -> bool:
                raise WebhookReplayStoreUnavailable("down")

            def reset(self) -> None:
                return

            def check_health(self) -> None:
                raise WebhookReplayStoreUnavailable("down")

        secret = "unit-test-secret"
        body = b"payload"
        timestamp = "1700000000"
        nonce = "nonce-12345678"
        signature = compute_signature(secret, timestamp, nonce, body)
        result = verify_webhook_request(
            tenant_key="sunrise",
            signing_secret=secret,
            timestamp_header=timestamp,
            nonce_header=nonce,
            signature_header=signature,
            body=body,
            now=1_700_000_000,
            replay_store=_UnavailableStore(),
        )
        assert result.ok is False
        assert result.failure == WebhookAuthFailure.REPLAY_STORE_UNAVAILABLE
        assert result.status_code == 503

    def test_redis_unavailable_http_returns_503(self, anon_client, db, monkeypatch):
        _enable_bulk_import(db)
        signing_key = "tenant-secret"
        _set_erp_webhook_secret(db, signing_key, monkeypatch=monkeypatch)

        class _UnavailableStore:
            def reserve_nonce(self, tenant_key: str, nonce: str) -> bool:
                raise WebhookReplayStoreUnavailable("down")

            def reset(self) -> None:
                return

            def check_health(self) -> None:
                raise WebhookReplayStoreUnavailable("down")

        reset_webhook_replay_store_for_tests(_UnavailableStore())

        response = _post_signed_webhook(
            anon_client,
            signing_key=signing_key,
            filename="scores.csv",
            content=b"admission_no\n",
            content_type="text/csv",
        )
        assert response.status_code == 503


class TestProductionWebhookReplayConfig:
    def test_production_requires_redis_backend_when_bulk_import_enabled(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.debug", False)
        monkeypatch.setattr("app.config.settings.erp_webhook_replay_backend", "memory")
        reset_webhook_replay_store_for_tests()
        tenants = [("sunrise", {"features": {"bulkImport": True}})]
        errors = validate_production_webhook_replay_config(tenants)
        assert any("ERP_WEBHOOK_REPLAY_BACKEND" in message for message in errors)

    def test_production_requires_redis_url_when_bulk_import_enabled(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.debug", False)
        monkeypatch.setattr("app.config.settings.erp_webhook_replay_backend", "redis")
        monkeypatch.setattr("app.config.settings.erp_webhook_replay_redis_url", "")
        reset_webhook_replay_store_for_tests()
        tenants = [("sunrise", {"features": {"bulkImport": True}})]
        errors = validate_production_webhook_replay_config(tenants)
        assert any("ERP_WEBHOOK_REPLAY_REDIS_URL" in message for message in errors)

    def test_production_skips_replay_requirements_without_bulk_import(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.debug", False)
        monkeypatch.setattr("app.config.settings.erp_webhook_replay_backend", "memory")
        reset_webhook_replay_store_for_tests()
        tenants = [("sunrise", {"features": {"bulkImport": False}})]
        assert validate_production_webhook_replay_config(tenants) == []
