"""Application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.account_policy import EnforcePasswordChangeMiddleware
from app.client_ip import TrustedProxyMiddleware
from app.config import settings
from app.csrf import apply_pending_csrf_cookie, bind_csrf_token_for_request, reject_invalid_csrf
from app.db import db_readiness, init_db
from app.deps import NotAuthenticatedError, WrongPortalRedirect, get_optional_user
from app.portals import PORTAL_HOME
from app.routers import (
    admin,
    auth,
    charts,
    consent,
    erp_webhook,
    exports,
    import_api,
    import_mapping,
    interventions,
    parent,
    parent_consent_page,
    platform,
    remarks,
    student,
    students,
    teacher,
)
from app.routers.auth import clear_session_cookie
from app.templating import templates
from app.tenant.middleware import TenantMiddleware

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _validate_production_security_config() -> None:
    if settings.debug:
        return

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.erp_webhook_security import validate_production_webhook_replay_config
    from app.models import Tenant
    from app.secrets import validate_deployed_erp_secrets
    from app.throttle import validate_production_login_throttle_config

    with SessionLocal() as db:
        rows = db.scalars(select(Tenant).where(Tenant.status == "active")).all()
        tenant_rows = [(tenant.key, tenant.settings_json) for tenant in rows]

    errors = validate_deployed_erp_secrets(tenant_rows)
    errors.extend(validate_production_webhook_replay_config(tenant_rows))
    errors.extend(validate_production_login_throttle_config())
    if errors:
        for message in errors:
            logger.error(message)
        raise RuntimeError(
            "Production security configuration is invalid; refusing to start."
        )


# Backward-compatible alias for tests and docs referencing the ERP startup hook.
_validate_production_erp_secrets = _validate_production_security_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _validate_production_security_config()
    logger.info("%s v%s started", settings.app_name, __version__)
    yield


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
)

app.add_middleware(EnforcePasswordChangeMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(TrustedProxyMiddleware)

app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

app.include_router(auth.router)
app.include_router(parent_consent_page.router)
app.include_router(parent.router)
app.include_router(student.router)
app.include_router(teacher.router)
app.include_router(platform.router)
app.include_router(admin.router)
app.include_router(charts.router)
app.include_router(exports.router)
app.include_router(students.router)
app.include_router(remarks.router)
app.include_router(consent.router)
app.include_router(interventions.router)
app.include_router(import_api.router)
app.include_router(import_mapping.router)
app.include_router(erp_webhook.router)


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    bind_csrf_token_for_request(request)
    if await reject_invalid_csrf(request):
        if request.url.path.startswith(("/api/", "/export/")):
            return JSONResponse(
                {"detail": "CSRF validation failed."},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return HTMLResponse("Forbidden.", status_code=status.HTTP_403_FORBIDDEN)
    response = await call_next(request)
    apply_pending_csrf_cookie(request, response)
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'",
    )
    return response


@app.exception_handler(NotAuthenticatedError)
async def handle_not_authenticated(request: Request, exc: NotAuthenticatedError):
    """API callers get a 401; page requests are redirected to the login screen."""
    if request.url.path.startswith(("/api/", "/export/")):
        return JSONResponse(
            {"detail": "Authentication required."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response


@app.exception_handler(WrongPortalRedirect)
async def handle_wrong_portal(request: Request, exc: WrongPortalRedirect):
    """Browser navigation to the wrong portal goes to the caller's own home."""
    return RedirectResponse(exc.location, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/", include_in_schema=False)
def root(request: Request) -> Response:
    user = None
    try:
        from app.db import SessionLocal

        with SessionLocal() as db:
            user = get_optional_user(request, db)
    except Exception:  # the landing page must redirect, never surface a 500
        user = None
    if user is not None:
        return RedirectResponse(
            PORTAL_HOME[user.role], status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Liveness: process is up (no dependency checks)."""
    return {"status": "ok", "version": __version__}


@app.get("/readyz", include_in_schema=False)
def readyz() -> Response:
    """Readiness: database reachable and Alembic revision at head."""
    readiness = db_readiness()
    ready = (
        readiness["database"] == "ok" and readiness["migrations"] == "ok"
    )
    body = {"status": "ok" if ready else "unavailable", **readiness}
    if not ready:
        return JSONResponse(body, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return JSONResponse(body)


@app.get("/api/tenant/context", include_in_schema=False)
def tenant_context(request: Request) -> Response:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return JSONResponse({"detail": "Not found."}, status_code=404)
    return JSONResponse(
        {
            "tenant": tenant.tenant_key,
            "displayName": tenant.display_name,
            "hostname": tenant.hostname,
        }
    )


@app.exception_handler(status.HTTP_404_NOT_FOUND)
async def not_found(request: Request, exc) -> Response:
    if request.url.path.startswith(("/api/", "/export/")):
        return JSONResponse({"detail": "Not found."}, status_code=404)
    return HTMLResponse(
        templates.get_template("error.html").render(
            request=request, code=404, message="We could not find that page."
        ),
        status_code=404,
    )
