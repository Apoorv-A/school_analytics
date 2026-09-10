# 2026-09-10 — SQLite connection pool exhaustion on dashboards

| Field | Value |
| --- | --- |
| **Repo** | school_analytics |
| **Area** | database / local-dev |
| **Severity** | high |
| **Status** | resolved |

## Symptoms

- Principal/admin dashboard took ~20–30 seconds to load after login.
- Terminal showed `sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 0 reached, connection timed out, timeout 30.00`.
- Stack trace pointed at `TenantMiddleware` → `SessionLocal()` while parallel `/api/charts/*` requests were in flight.
- `favicon.ico` 404 in logs (benign; fixed separately with a 204 route).

## Cause

Local demo uses SQLite with SQLAlchemy `QueuePool` (`pool_size=5`, `max_overflow=0`). The admin dashboard fires **6+ parallel chart API requests**. Each request holds a DB connection via `get_db()`. The sixth request blocked until the 30s pool timeout.

## Effects

- App appeared broken or extremely slow locally.
- Misleading stack traces suggested middleware/tenant lookup failure; real issue was pool starvation under concurrency.

## Resolution

- `app/db.py`: use `NullPool` for SQLite so each checkout gets a fresh connection and releases immediately.
- PostgreSQL production path unchanged (`pool_size=10`, `max_overflow=20`).
- `app/main.py`: return 204 for `/favicon.ico`.
- `app/tenant/middleware.py`: exempt `/favicon.ico` from tenant lookup.
- Documented in `docs/AI_HANDOFF.md` and `docs/architecture/multi-tenancy.md`.

## Prevention

- When adding parallel client fetches to a page, load-test locally with SQLite.
- Watch for `QueuePool limit` in uvicorn logs.
- Prefer `NullPool` (or adequate pool + overflow) for SQLite in async/multi-request servers.

## References

- Fix: `app/db.py`, commits on 2026-09-10
- CI: preflight green after subsequent Ruff fixes
