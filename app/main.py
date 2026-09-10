"""Application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.db import check_db_connection, init_db
from app.deps import NotAuthenticatedError, get_optional_user
from app.portals import PORTAL_HOME
from app.routers import (
    admin,
    auth,
    charts,
    erp_webhook,
    exports,
    import_api,
    parent,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("%s v%s started", settings.app_name, __version__)
    yield


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
)

app.add_middleware(TenantMiddleware)

app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

app.include_router(auth.router)
app.include_router(parent.router)
app.include_router(student.router)
app.include_router(teacher.router)
app.include_router(admin.router)
app.include_router(platform.router)
app.include_router(charts.router)
app.include_router(exports.router)
app.include_router(students.router)
app.include_router(remarks.router)
app.include_router(import_api.router)
app.include_router(erp_webhook.router)


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
    db_ok = check_db_connection()
    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "database": "ok" if db_ok else "unavailable",
    }


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
