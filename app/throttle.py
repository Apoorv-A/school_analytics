"""Shared login-throttle storage.

Production multi-replica deployments must use a shared backend (Redis) so
lockout counts are consistent across pods. Local dev and tests may use the
in-memory implementation when ``DEBUG=true`` or when tests inject a store.

Configure via ``LOGIN_THROTTLE_BACKEND`` (``memory`` or ``redis``).
When ``redis`` is selected, set ``LOGIN_THROTTLE_REDIS_URL``.
In production (``DEBUG=false``), ``redis`` is required and Redis failures fail closed.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 8
_LOCKOUT_SECONDS = 300
_REDIS_KEY_PREFIX = "sa:login_throttle:"


class LoginThrottleStoreUnavailable(Exception):
    """Shared throttle store cannot accept login attempt accounting."""


class LoginThrottleStore(ABC):
    @abstractmethod
    def is_locked_out(self, key: str) -> bool: ...

    @abstractmethod
    def record_failure(self, key: str) -> bool:
        """Record one failed attempt. Return True when lockout threshold is reached."""

    @abstractmethod
    def clear_failures(self, key: str) -> None: ...

    def check_health(self) -> None:
        """Verify the store can accept throttle operations (production startup)."""
        return

    def reset(self) -> None:
        """Clear all throttle state (tests only)."""
        return


class InMemoryLoginThrottleStore(LoginThrottleStore):
    """Process-local throttle store (dev / single replica / unit tests)."""

    def __init__(self) -> None:
        self._attempts: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def _get_record(self, key: str) -> tuple[int, float]:
        record = self._attempts.get(key)
        if record is None:
            return 0, time.monotonic()
        count, first_seen = record
        if time.monotonic() - first_seen > _LOCKOUT_SECONDS:
            return 0, time.monotonic()
        return count, first_seen

    def is_locked_out(self, key: str) -> bool:
        with self._lock:
            count, _ = self._get_record(key)
            return count >= _MAX_ATTEMPTS

    def record_failure(self, key: str) -> bool:
        with self._lock:
            count, first_seen = self._get_record(key)
            count += 1
            self._attempts[key] = (count, first_seen)
            return count >= _MAX_ATTEMPTS

    def clear_failures(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


class RedisLoginThrottleStore(LoginThrottleStore):
    """Redis-backed shared throttle for multi-replica production."""

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
            logger.error("Redis login throttle store unavailable: %s", exc)
            return None
        return client

    def _redis_key(self, key: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{key}"

    def _require_client(self) -> Redis:
        if self._client is None:
            raise LoginThrottleStoreUnavailable(
                "Redis login throttle store is not connected"
            )
        return self._client

    def is_locked_out(self, key: str) -> bool:
        try:
            client = self._require_client()
            count = client.get(self._redis_key(key))
            if count is None:
                return False
            return int(count) >= _MAX_ATTEMPTS
        except LoginThrottleStoreUnavailable:
            if self._fail_closed:
                raise
            return False
        except Exception as exc:
            logger.warning("Redis login throttle read failed: %s", exc)
            if self._fail_closed:
                raise LoginThrottleStoreUnavailable(
                    "Redis login throttle read failed"
                ) from exc
            return False

    def record_failure(self, key: str) -> bool:
        try:
            client = self._require_client()
            redis_key = self._redis_key(key)
            count = client.incr(redis_key)
            if count == 1:
                client.expire(redis_key, _LOCKOUT_SECONDS)
            return int(count) >= _MAX_ATTEMPTS
        except LoginThrottleStoreUnavailable:
            if self._fail_closed:
                raise
            return False
        except Exception as exc:
            logger.warning("Redis login throttle write failed: %s", exc)
            if self._fail_closed:
                raise LoginThrottleStoreUnavailable(
                    "Redis login throttle write failed"
                ) from exc
            return False

    def clear_failures(self, key: str) -> None:
        try:
            client = self._require_client()
            client.delete(self._redis_key(key))
        except LoginThrottleStoreUnavailable:
            if self._fail_closed:
                raise
        except Exception as exc:
            logger.warning("Redis login throttle clear failed: %s", exc)
            if self._fail_closed:
                raise LoginThrottleStoreUnavailable(
                    "Redis login throttle clear failed"
                ) from exc

    def check_health(self) -> None:
        client = self._require_client()
        client.ping()

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


_store: LoginThrottleStore | None = None


def get_login_throttle_store() -> LoginThrottleStore:
    global _store
    if _store is None:
        backend = settings.login_throttle_backend.strip().lower()
        if backend == "redis":
            redis_url = settings.login_throttle_redis_url.strip()
            if not redis_url:
                if settings.debug:
                    logger.warning(
                        "LOGIN_THROTTLE_BACKEND=redis without "
                        "LOGIN_THROTTLE_REDIS_URL; using in-memory fallback"
                    )
                    _store = InMemoryLoginThrottleStore()
                else:
                    raise RuntimeError(
                        "LOGIN_THROTTLE_REDIS_URL must be set when "
                        "LOGIN_THROTTLE_BACKEND=redis in production."
                    )
            else:
                _store = RedisLoginThrottleStore(
                    redis_url,
                    fail_closed=not settings.debug,
                )
        elif backend == "memory":
            _store = InMemoryLoginThrottleStore()
        else:
            raise RuntimeError(
                f"Unsupported LOGIN_THROTTLE_BACKEND: {backend!r}"
            )
    return _store


def reset_login_throttle_store_for_tests(
    store: LoginThrottleStore | None = None,
) -> None:
    """Allow tests to reset or inject the process-global throttle store."""
    global _store
    _store = store if store is not None else InMemoryLoginThrottleStore()


def validate_production_login_throttle_config() -> list[str]:
    """Return configuration errors for production login throttle settings."""
    if settings.debug:
        return []

    backend = settings.login_throttle_backend.strip().lower()
    redis_url = settings.login_throttle_redis_url.strip()
    errors: list[str] = []

    if backend != "redis":
        errors.append(
            "LOGIN_THROTTLE_BACKEND must be 'redis' when DEBUG is false."
        )
        return errors

    if not redis_url:
        errors.append(
            "LOGIN_THROTTLE_REDIS_URL must be set when LOGIN_THROTTLE_BACKEND=redis."
        )
        return errors

    try:
        store = get_login_throttle_store()
        if isinstance(store, RedisLoginThrottleStore):
            store.check_health()
    except Exception as exc:
        errors.append(f"Login throttle Redis is unavailable: {exc}")

    return errors
