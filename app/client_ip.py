"""Client IP extraction with optional trusted-proxy support.

When ``TRUSTED_PROXY`` is enabled, ``X-Forwarded-For`` is honoured (leftmost hop).
Otherwise the direct connection peer is used so clients cannot spoof their IP.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


def client_host(request: Request) -> str:
    """Return the client IP for rate limiting and audit metadata."""
    stored = getattr(request.state, "client_host", None)
    if isinstance(stored, str) and stored:
        return stored
    if request.client:
        return request.client.host
    return "unknown"


class TrustedProxyMiddleware(BaseHTTPMiddleware):
    """Populate ``request.state.client_host`` when behind a trusted reverse proxy."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if settings.trusted_proxy:
            forwarded = request.headers.get("X-Forwarded-For", "").strip()
            if forwarded:
                request.state.client_host = forwarded.split(",")[0].strip() or "unknown"
            elif request.client:
                request.state.client_host = request.client.host
            else:
                request.state.client_host = "unknown"
        return await call_next(request)
