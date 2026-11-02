"""Minimal shared in-memory Redis stand-in for multi-client integration tests.

Uses a shared ``FakeServer`` so two store instances behave like separate pods
talking to the same Redis. Avoids requiring ``fakeredis`` in partial local venvs;
CI still installs ``fakeredis`` from ``requirements.lock`` when desired.
"""

from __future__ import annotations

import threading
import time


class FakeServer:
    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._expires: dict[str, float] = {}
        self._lock = threading.Lock()


class FakeRedis:
    def __init__(self, *, server: FakeServer, decode_responses: bool = True) -> None:
        self._server = server
        self._decode = decode_responses

    def ping(self) -> bool:
        return True

    def _purge_if_expired(self, key: str) -> None:
        expires_at = self._server._expires.get(key)
        if expires_at is not None and time.monotonic() >= expires_at:
            self._server._strings.pop(key, None)
            self._server._expires.pop(key, None)

    def incr(self, key: str) -> int:
        with self._server._lock:
            self._purge_if_expired(key)
            current = int(self._server._strings.get(key, "0"))
            current += 1
            self._server._strings[key] = str(current)
            return current

    def expire(self, key: str, seconds: int) -> bool:
        with self._server._lock:
            if key not in self._server._strings:
                return False
            self._server._expires[key] = time.monotonic() + seconds
            return True

    def get(self, key: str) -> str | None:
        with self._server._lock:
            self._purge_if_expired(key)
            return self._server._strings.get(key)

    def delete(self, key: str) -> int:
        with self._server._lock:
            existed = key in self._server._strings
            self._server._strings.pop(key, None)
            self._server._expires.pop(key, None)
            return 1 if existed else 0

    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        with self._server._lock:
            self._purge_if_expired(key)
            if nx and key in self._server._strings:
                return False
            self._server._strings[key] = value
            if ex is not None:
                self._server._expires[key] = time.monotonic() + ex
            return True
