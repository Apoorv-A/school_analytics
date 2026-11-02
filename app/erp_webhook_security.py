"""ERP webhook HMAC verification and replay protection.

Production multi-replica deployments must use a shared nonce store (Redis)
so replay checks are consistent across pods. Local dev and tests may use the
in-memory implementation when ``DEBUG=true`` or when tests inject a store.

Configure via ``ERP_WEBHOOK_REPLAY_BACKEND`` (``memory`` or ``redis``).
When ``redis`` is selected, set ``ERP_WEBHOOK_REPLAY_REDIS_URL``.
In production (``DEBUG=false``), ``redis`` is required and Redis failures fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)

HEADER_TIMESTAMP = "X-SA-Webhook-Timestamp"
HEADER_NONCE = "X-SA-Webhook-Nonce"
HEADER_SIGNATURE = "X-SA-Webhook-Signature"

_SIGNATURE_PREFIX = "v1="
_MAX_TIMESTAMP_SKEW_SECONDS = 300
_NONCE_TTL_SECONDS = 600
_MIN_NONCE_LENGTH = 8
_MAX_NONCE_LENGTH = 128
_REDIS_KEY_PREFIX = "sa:webhook_replay:"


class WebhookReplayStoreUnavailable(Exception):
    """Shared replay store cannot accept nonce reservations."""


class WebhookAuthFailure(StrEnum):
    MISSING_SIGNING_KEY = "missing_signing_key"
    MISSING_HEADERS = "missing_headers"
    INVALID_TIMESTAMP = "invalid_timestamp"
    TIMESTAMP_EXPIRED = "timestamp_expired"
    INVALID_NONCE = "invalid_nonce"
    REPLAY = "replay"
    INVALID_SIGNATURE = "invalid_signature"
    REPLAY_STORE_UNAVAILABLE = "replay_store_unavailable"


@dataclass(frozen=True)
class WebhookAuthResult:
    ok: bool
    failure: WebhookAuthFailure | None = None
    status_code: int = 401


class WebhookReplayStore(ABC):
    @abstractmethod
    def reserve_nonce(self, tenant_key: str, nonce: str) -> bool:
        """Return True when nonce is new; False when replayed."""

    @abstractmethod
    def reset(self) -> None: ...

    def check_health(self) -> None:
        """Verify the store can accept reservations (production startup)."""
        return


class InMemoryWebhookReplayStore(WebhookReplayStore):
    """Process-local nonce store (dev / single replica / unit tests)."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, seen_at in self._seen.items()
            if now - seen_at > _NONCE_TTL_SECONDS
        ]
        for key in expired:
            self._seen.pop(key, None)

    def reserve_nonce(self, tenant_key: str, nonce: str) -> bool:
        key = f"{tenant_key}:{nonce}"
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            if key in self._seen:
                return False
            self._seen[key] = now
            return True

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()

    def check_health(self) -> None:
        return


class RedisWebhookReplayStore(WebhookReplayStore):
    """Redis-backed shared replay store for multi-replica production."""

    def __init__(
        self,
        redis_url: str,
        *,
        client: Redis | None = None,
        fail_closed: bool = True,
    ) -> None:
        self._redis_url = redis_url
        self._fail_closed = fail_closed
        self._client: Redis | None = client
        if self._client is None:
            self._client = self._connect_client(redis_url)

    @staticmethod
    def _connect_client(redis_url: str) -> Redis | None:
        try:
            import redis
        except ImportError:
            logger.error("redis package is not installed")
            return None

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        try:
            client.ping()
        except Exception as exc:
            logger.error("Redis replay store unavailable: %s", exc)
            return None
        return client

    def _redis_key(self, tenant_key: str, nonce: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{tenant_key}:{nonce}"

    def _require_client(self) -> Redis:
        if self._client is None:
            raise WebhookReplayStoreUnavailable("Redis replay store is not connected")
        return self._client

    def reserve_nonce(self, tenant_key: str, nonce: str) -> bool:
        try:
            client = self._require_client()
            reserved = client.set(
                self._redis_key(tenant_key, nonce),
                "1",
                nx=True,
                ex=_NONCE_TTL_SECONDS,
            )
            return bool(reserved)
        except WebhookReplayStoreUnavailable:
            if self._fail_closed:
                raise
            return False
        except Exception as exc:
            logger.warning("Redis replay reservation failed: %s", exc)
            if self._fail_closed:
                raise WebhookReplayStoreUnavailable(
                    "Redis replay reservation failed"
                ) from exc
            return False

    def reset(self) -> None:
        if self._client is None:
            return
        cursor = 0
        pattern = f"{_REDIS_KEY_PREFIX}*"
        while True:
            cursor, keys = self._client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                self._client.delete(*keys)
            if cursor == 0:
                break

    def check_health(self) -> None:
        client = self._require_client()
        client.ping()


_store: WebhookReplayStore | None = None


def get_webhook_replay_store() -> WebhookReplayStore:
    global _store
    if _store is None:
        backend = settings.erp_webhook_replay_backend.strip().lower()
        if backend == "redis":
            redis_url = settings.erp_webhook_replay_redis_url.strip()
            if not redis_url:
                if settings.debug:
                    logger.warning(
                        "ERP_WEBHOOK_REPLAY_BACKEND=redis without "
                        "ERP_WEBHOOK_REPLAY_REDIS_URL; using in-memory fallback"
                    )
                    _store = InMemoryWebhookReplayStore()
                else:
                    raise RuntimeError(
                        "ERP_WEBHOOK_REPLAY_REDIS_URL must be set when "
                        "ERP_WEBHOOK_REPLAY_BACKEND=redis in production."
                    )
            else:
                _store = RedisWebhookReplayStore(
                    redis_url,
                    fail_closed=not settings.debug,
                )
        elif backend == "memory":
            _store = InMemoryWebhookReplayStore()
        else:
            raise RuntimeError(
                f"Unsupported ERP_WEBHOOK_REPLAY_BACKEND: {backend!r}"
            )
    return _store


def reset_webhook_replay_store_for_tests(store: WebhookReplayStore | None = None) -> None:
    """Allow tests to reset or inject the process-global replay store."""
    global _store
    _store = store if store is not None else InMemoryWebhookReplayStore()


def validate_production_webhook_replay_config(
    tenants: list[tuple[str, dict | None]],
) -> list[str]:
    """Return configuration errors for production webhook replay settings."""
    if settings.debug:
        return []

    has_bulk_import = any(
        bool(
            ((settings_payload or {}).get("features") or {}).get("bulkImport", False)
        )
        for _, settings_payload in tenants
    )
    backend = settings.erp_webhook_replay_backend.strip().lower()
    redis_url = settings.erp_webhook_replay_redis_url.strip()

    if backend == "redis" and redis_url:
        try:
            store = get_webhook_replay_store()
            if isinstance(store, RedisWebhookReplayStore):
                store.check_health()
        except Exception as exc:
            return [f"ERP webhook replay Redis is unavailable: {exc}"]

    if not has_bulk_import:
        return []

    errors: list[str] = []
    if backend != "redis":
        errors.append(
            "ERP_WEBHOOK_REPLAY_BACKEND must be 'redis' when bulk import is enabled "
            "and DEBUG is false."
        )
        return errors

    if not redis_url:
        errors.append(
            "ERP_WEBHOOK_REPLAY_REDIS_URL must be set when bulk import is enabled."
        )

    return errors


def resolve_signing_secret(
    *,
    webhook_signing_secret_ref: str,
    webhook_secret_ref: str,
) -> str:
    """Resolve the ERP webhook signing key from deployment secret references."""
    from app.secrets import resolve_erp_webhook_signing_secret

    return resolve_erp_webhook_signing_secret(
        webhook_secret_ref=webhook_secret_ref,
        webhook_signing_secret_ref=webhook_signing_secret_ref,
        required=True,
    )


def _signed_payload(timestamp: str, nonce: str, body: bytes) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    return f"{timestamp}.{nonce}.{body_digest}".encode()


def compute_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        _signed_payload(timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify_webhook_request(
    *,
    tenant_key: str,
    signing_secret: str,
    timestamp_header: str | None,
    nonce_header: str | None,
    signature_header: str | None,
    body: bytes,
    replay_store: WebhookReplayStore | None = None,
    now: int | None = None,
) -> WebhookAuthResult:
    """Validate ERP webhook HMAC signature and replay controls."""
    if not signing_secret:
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.MISSING_SIGNING_KEY,
            status_code=401,
        )

    if not timestamp_header or not nonce_header or not signature_header:
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.MISSING_HEADERS,
            status_code=401,
        )

    timestamp_text = timestamp_header.strip()
    nonce = nonce_header.strip()
    signature = signature_header.strip()

    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.INVALID_TIMESTAMP,
            status_code=401,
        )

    current = now if now is not None else int(time.time())
    if abs(current - timestamp) > _MAX_TIMESTAMP_SKEW_SECONDS:
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.TIMESTAMP_EXPIRED,
            status_code=401,
        )

    if not (_MIN_NONCE_LENGTH <= len(nonce) <= _MAX_NONCE_LENGTH):
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.INVALID_NONCE,
            status_code=401,
        )

    expected = compute_signature(signing_secret, timestamp_text, nonce, body)
    if not signature.startswith(_SIGNATURE_PREFIX):
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.INVALID_SIGNATURE,
            status_code=401,
        )
    supplied = signature.removeprefix(_SIGNATURE_PREFIX)
    if not hmac.compare_digest(supplied, expected.removeprefix(_SIGNATURE_PREFIX)):
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.INVALID_SIGNATURE,
            status_code=401,
        )

    store = replay_store or get_webhook_replay_store()
    try:
        if not store.reserve_nonce(tenant_key, nonce):
            return WebhookAuthResult(
                ok=False,
                failure=WebhookAuthFailure.REPLAY,
                status_code=403,
            )
    except WebhookReplayStoreUnavailable:
        return WebhookAuthResult(
            ok=False,
            failure=WebhookAuthFailure.REPLAY_STORE_UNAVAILABLE,
            status_code=503,
        )

    return WebhookAuthResult(ok=True)


def _parse_boundary(content_type: str) -> str | None:
    match = re.search(r"boundary=([^;]+)", content_type, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().strip('"')


def extract_multipart_file(content_type: str, body: bytes) -> tuple[str, bytes] | None:
    """Extract the first multipart field named ``file``."""
    boundary = _parse_boundary(content_type)
    if not boundary:
        return None
    delimiter = f"--{boundary}".encode()
    for part in body.split(delimiter):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        chunk = part
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"--\r\n"):
            chunk = chunk[:-4]
        elif chunk.endswith(b"--"):
            chunk = chunk[:-2]
        header_end = chunk.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        headers = chunk[:header_end].decode("latin-1", errors="replace")
        if 'name="file"' not in headers:
            continue
        content = chunk[header_end + 4 :]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        filename_match = re.search(r'filename="([^"]*)"', headers)
        filename = filename_match.group(1) if filename_match else "upload"
        return filename, content
    return None
