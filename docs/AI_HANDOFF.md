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
| `app/tenant/features.py` | Feature flag guards (exports, portals, remarks) |
| `app/db.py` | SQLAlchemy engine; SQLite `NullPool` locally, pooled Postgres in production |
| `app/deps.py` | Auth + `AccessScope` |
| `app/analytics/` | Metrics, scoped queries, chart registry |
| `tenant_config/models.py` | Canonical Pydantic tenant config (JSON Schema source) |
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

**Hostnames:** seeded tenants are `sunrise.localhost` (full school) and `horizon.localhost` (minimal second tenant). On macOS, `*.localhost` usually resolves to `127.0.0.1` without editing `/etc/hosts`. If not, add `127.0.0.1 sunrise.localhost horizon.localhost`.

- http://sunrise.localhost:8000 — demo password `Demo@12345` (when `DEMO_PASSWORD` matches seed)
- http://horizon.localhost:8000 — same password; overlapping admin email proves per-tenant login scope

**SQLite pooling:** local demo uses `NullPool` in `app/db.py` so parallel chart API calls on dashboards do not exhaust a fixed connection pool (symptom: 20–30s load, `QueuePool limit` errors in logs). Production PostgreSQL uses a normal sized pool.

**Do not confuse passwords:** `Demo@12345` is the **web app** demo login. Your **Mac password** is only for `sudo` (e.g. editing `/etc/hosts`).

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

## Tests

~294 tests including `tests/test_tenant_isolation.py` (hostname resolution, cross-tenant session, per-tenant lockout, overlapping emails).

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

**Last updated:** 2026-09-10 (learnings log, CI notes, SQLite NullPool, RLS/throttle fixes).
