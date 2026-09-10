# AI Handoff — School Analytics Platform

Use this document to onboard another AI agent, reviewer, or engineer. It is the single canonical context file for the **Python multi-tenant** implementation.

**Path:** `docs/AI_HANDOFF.md` in the [school_analytics](https://github.com/Apoorv-A/school_analytics) code repository.

---

## Repository split (critical)

| Repository | Purpose | Clone |
| --- | --- | --- |
| **school_analytics** (this repo) | Application code, tests, deploy chart, tenant operator CLI, generated docs | `Apoorv-A/school_analytics` |
| **school-analytics-config** | Tenant YAML per school/environment, JSON Schema, CI deploy/promote/validate | `Apoorv-A/school-analytics-config` |

Do **not** expect `platform/` (legacy Java) or `config/` inside the code repo. Configuration is a separate repository.

**Local workspace hygiene:** clone config beside the code repo. The code repo `.gitignore` ignores nested `config/` and `platform/`. Remove any leftover nested checkouts:

```bash
git clone https://github.com/Apoorv-A/school-analytics-config.git ../school-analytics-config
rm -rf platform   # legacy Java only
```

---

## Product summary

Multi-tenant school analytics SaaS: one shared FastAPI deployment per environment, four portals (parent, student, teacher, principal), full-year assessment analytics, Chart.js visualizations, server-rendered Jinja2 UI.

**Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, PostgreSQL (production) / SQLite (local tests), Alembic, Pydantic v2, pytest.

---

## Tenancy model

1. **Hostname binding** — `TenantMiddleware` resolves tenant from `Host` header via `tenant_domains.hostname`. Unknown host → 404.
2. **Session binding** — Cookie token includes `tenant_id` (`tid` claim). Login scopes email to `(tenant_id, email)`.
3. **Application scope** — `AccessScope` in `app/deps.py` enforces role boundaries within a tenant. Use `assert_tenant_record()` when loading rows by primary key.
4. **PostgreSQL RLS** — `SET LOCAL app.tenant_id` on each transaction; policies on **school-owned tables only**.

### Registry tables excluded from RLS

`tenants` and `tenant_domains` have **no RLS**. They are read before request tenant context exists (middleware hostname lookup, tenant operator reconcile). Applying RLS there breaks bootstrap.

---

## Key modules

| Path | Role |
| --- | --- |
| `app/tenant/middleware.py` | Hostname → `TenantContext` |
| `app/tenant/context.py` | Request-scoped tenant context (ContextVar) |
| `app/tenant/settings.py` | Per-tenant thresholds and feature flags from `tenants.settings_json` |
| `app/tenant/rls.py` | Postgres RLS setup (data tables only) |
| `app/tenant/features.py` | Feature flag guards (exports, portals, remarks, bulkImport) |
| `app/import_/` | CSV validate/apply CLI (`python -m app.import_.cli validate|apply`) — underscore because `import` is reserved |
| `app/import_/history.py` | Import run audit log (`import_runs` table) |
| `app/routers/students.py` | Admin student search (`GET /api/students/search`) |
| `app/routers/remarks.py` | Teacher remark POST (`POST /api/remarks`) |
| `app/db.py` | SQLAlchemy engine; SQLite `NullPool` locally, pooled Postgres in production |
| `app/deps.py` | Auth + `AccessScope` |
| `app/analytics/` | Metrics, scoped queries, chart registry |
| `tenant_config/models.py` | Canonical Pydantic tenant config (tenant/school/academics + telemetry/resources/image deploy sections) |
| `tenant_operator/` | CLI: `validate`, `apply`, `schema` |
| `seed/generate.py` | Demo data: **sunrise** (full) + **horizon** (minimal, overlapping email) |

---

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set SECRET_KEY, DEMO_MODE=true, DEMO_PASSWORD=Demo@12345 (must match seed)

python -m seed.generate --reset
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**After pulling schema changes** (new Alembic revisions), either apply migrations or rebuild demo data:

```bash
alembic upgrade head
# or wipe and reseed:
python -m seed.generate --reset
```

SQLite local dev runs `alembic upgrade head` automatically on app startup via `init_db()`, so a simple server restart usually suffices. Use the commands above if you prefer to migrate or reseed before starting the server.

**Hostnames:** seeded tenants are `sunrise.localhost` (full school) and `horizon.localhost` (minimal second tenant). On macOS, `*.localhost` usually resolves to `127.0.0.1` without editing `/etc/hosts`. If not, add `127.0.0.1 sunrise.localhost horizon.localhost`.

- http://sunrise.localhost:8000 — demo password `Demo@12345` (when `DEMO_PASSWORD` matches seed)
- http://horizon.localhost:8000 — same password; overlapping admin email proves per-tenant login scope

**SQLite pooling:** local demo uses `NullPool` in `app/db.py` so parallel chart API calls on dashboards do not exhaust a fixed connection pool (symptom: 20–30s load, `QueuePool limit` errors in logs). Production PostgreSQL uses a normal sized pool.

**SQLite WAL disk I/O error:** if `alembic upgrade head` or startup fails with `sqlite3.OperationalError: disk I/O error` on `PRAGMA journal_mode=WAL`, stop the app (uvicorn) and remove stale sidecars, then retry:

```bash
rm -f school.db-wal school.db-shm
alembic upgrade head
```

The app falls back to `DELETE` journal mode if WAL is unavailable; clearing sidecars restores normal WAL behavior. See [docs/learnings/sqlite-wal-disk-io-error.md](learnings/sqlite-wal-disk-io-error.md).

**Do not confuse passwords:** `Demo@12345` is the **web app** demo login. Your **Mac password** is only for `sudo` (e.g. editing `/etc/hosts`).

**Filters:** `FilterParams` supports `subject_ids` / `section_ids` (repeated query params, max 5 / 4) for API and bookmark power users. The dashboard filter bar uses single-select `subject_id` / `section_id` only. Legacy singular ids and list fields coexist; list fields win when both are set. Chart handlers treat a lone filter-bar class pick as a highlight/focus hint on comparison charts (leaderboard, class comparison) rather than collapsing them to one row. `AccessScope.narrow(filters, db)` enforces scope and same-grade section picks.

**Import:**

```bash
PYTHONPATH=. python -m app.import_.cli validate --dir=/path/to/csv-bundle
# Use the tenant UUID from the tenants table (do not wrap in angle brackets — zsh treats <> as globs):
PYTHONPATH=. python -m app.import_.cli apply --dir=/path/to/csv-bundle --tenant-id=11111111-2222-3333-4444-555555555555
```

Admin UI: `/admin/import` when tenant `features.bulkImport` is true. Bootstrap principal: set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` before `tenant_operator apply`.

**Import user passwords:** `users.csv` apply creates one random temporary password per new account (`secrets.token_urlsafe(16)`). CLI and admin `/api/import/apply` responses include `created_users: [{email, temporary_password}]` once — not stored in `import_runs`. ERP webhook apply does not return passwords; reset via admin if needed.

**ERP stance:** analytics stays read-mostly; CSV ingest + `external_id` correlation — see [docs/product/erp-strategy.md](product/erp-strategy.md).

Tests:

```bash
PYTHONPATH=. pytest -q
```

Docs generation:

```bash
PYTHONPATH=. python scripts/docs_generate.py
PYTHONPATH=. python scripts/docs_verify.py
```

---

## Config repo workflow

1. Edit `tenants/<tenant>/<env>.yaml` in **school-analytics-config**.
2. PR runs `validate-config` (JSON Schema + Python `tenant_operator validate`).
3. Deploy via protected tag or environment release (see config repo README).
4. Operator reconciles tenant registry (from code repo, config file in sibling checkout):

```bash
PYTHONPATH=. python -m tenant_operator apply --config=../school-analytics-config/tenants/sunrise/dev.yaml
```

Environment image versions live in `environments/<env>/release.yaml`, not per-tenant files.

**Config CI:** `validate-config` checks out private `school_analytics` using GitHub secret `CONFIG_PR_TOKEN` on the config repo (see config repo README).

---

## Security invariants (do not break)

- Out-of-scope IDs return **404**, not 403.
- Parent `cohort_view()` is aggregate-only; never return classmate names to parents.
- Login throttle key includes **`tenant_id`** — lockout is per school, not global per email.
- Never put secrets in tenant YAML; K8s secret references only.
- Session `max_age` comes from tenant settings when context is set.

---

## Filters

- Dashboard filter bar: single-select `subject_id` and `section_id` dropdowns (all roles).
- API / query-string power users: repeated `subject_ids` / `section_ids` (max 5 subjects, 4 sections); `subject_id` / `section_id` kept for backward compatibility; list fields win when both are set.
- Comparison charts (`school.section_compare`, `school.section_leaderboard`): a lone filter-bar `section_id` highlights that class but still ranks/compares all in-scope classes; two or more explicit `section_ids` narrow the set.
- `AccessScope.narrow()` validates counts, scope, and same-grade rule for sections.

## Import

```bash
PYTHONPATH=. python -m app.import_.cli validate --dir=./csv-bundle
PYTHONPATH=. python -m app.import_.cli apply --dir=./csv-bundle --tenant-id=11111111-2222-3333-4444-555555555555
```

Admin upload at `/admin/import` when tenant `features.bulkImport` is true.

Bootstrap principal: operator env `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` on `tenant_operator apply`.

New users from `users.csv` get unique temporary passwords returned in the apply report (`created_users`) for CLI and admin apply only; webhook ingest omits credentials.

## Tests

~300 tests including tenant isolation, authz, charts, insights, and import validation.

Always pass `Host: sunrise.localhost` (or `horizon.localhost`) in API tests — see `tests/conftest.py` `DEFAULT_HEADERS`.

---

## Deployment artifacts

| Path | Purpose |
| --- | --- |
| `deploy/Dockerfile` | Production image |
| `deploy/docker-compose.yml` | Local Postgres + app |
| `deploy/helm/school-analytics/` | Shared K8s release |
| `.github/workflows/preflight.yml` | CI: ruff, pytest, docs drift, helm lint |
| `.github/workflows/release.yml` | Image publish on `v*` tag |

---

## Legacy note

A Java/React platform previously existed in a separate `school-analytics-platform` repo and was checked out as `platform/` beside this project. **Production path is Python-only.** Do not reintroduce Java coupling in config CI or docs.

---

## Documentation index

See [docs/README.md](README.md) for product, architecture, ADRs, generated column dictionary, and chart catalog.

### Operational learnings (failure log)

When something breaks in dev or CI, add an entry under **[docs/learnings/](learnings/README.md)** — one markdown file per incident (symptoms, cause, effects, resolution). Config-repo incidents live in **school-analytics-config/docs/learnings/**.

### CI before push

```bash
ruff check app seed tests tenant_config tenant_operator scripts
PYTHONPATH=. pytest -q
PYTHONPATH=. python scripts/docs_verify.py
```

**Last updated:** 2026-09-10 (Phase 2: import fixes, parent insight summary, ERP webhook).

---

## Phase 2 (in progress)

Scope and roadmap: [docs/product/phase2.md](product/phase2.md) and [docs/product/erp-strategy.md](product/erp-strategy.md).

**Shipped so far:**

- `import_runs` audit table (Alembic `004_import_runs`) — records validate/apply from CLI, `/admin/import`, `/api/import/*`, and `/api/erp/import`.
- Admin **Data import** page lists recent runs (status, source, files, error count).
- Admin filter bar: student **combobox** (focus opens roster browse via `GET /api/students/search?q=`, typing filters); **subject** and **class** are single-select dropdowns (`subject_id`, `section_id`).
- **Import apply** — `school_structure.csv` (grades/sections/subjects), score upsert by `assessment_code` → `assessments.external_id`, extract-error guards, 5 MB per-file / 25 MB bundle / 50 MB zip decompressed caps, temp dir cleanup.
- **Parent-safe insights** — `student.insight_summary` chart on parent/student overview (no class averages or staff actions); admin keeps `student.insights`.
- **ERP webhook** — `POST /api/erp/import` with `Authorization: Bearer <token>` when `bulkImport` is enabled. Each tenant must store its own secret in `settings_json.erp.webhookToken`; missing/empty tenant secret always returns 401 (no global env fallback).

**Up next:** Scheduled SFTP/REST connectors, delta sync, conflict resolution UI.

---

## Filter bar (admin UX)

- **Student:** combobox on admin pages — click/focus opens browse list (`q=` empty, up to 50 rows); typing filters by name/admission/email prefix; selection navigates to `/admin/student/{id}` on lookup/drill-down routes. Dropdown stacks above chart cards (`.filters` z-index + `.is-open` on combobox). **Student lookup** (`/admin/student`) sets `require_student`: charts stay empty until a student is picked (no roster default on that page).
- **Subject / class:** single-select dropdowns for all roles (`subject_id`, `section_id`). A selected class highlights that row on the class leaderboard and keeps every in-scope class visible on the class comparison chart.
- **Multi-select via API:** repeated `subject_ids` / `section_ids` query params still work for bookmarks, exports, and integrations; `_normalize_filter_controls()` maps a lone list value onto the singular dropdown field when rendering a page.
- Non-admin roles use the same singular dropdown fields (no combobox on student picker except admin).

---
