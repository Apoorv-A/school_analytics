# AI Handoff — School Analytics Platform

Use this document to onboard another AI agent, reviewer, or engineer. It is the single canonical context file for the **Python multi-tenant** implementation.

**Authoritative audit handoff (P0 gates, invariants, backlog):** The September 2026 engineering audit handoff (`school_platform_ai_handoff.md`) is the release-sequencing source of truth. That file is **not vendored in this repository**; it may live in an external audit bundle. Do not begin post-P0 feature work until P0 exit criteria there are satisfied. This file documents the current codebase; when they conflict on sequencing or security invariants, follow the audit handoff. The §12.1 table below is the in-repo checklist.

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

Analytics is a **school-provided service** to families under the school’s policies; the app does not gate parent/student dashboards on separate in-app consent (see `docs/product/phase2.md`).

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
| `app/tenant/features.py` | Feature flag guards (exports, portals, remarks, bulkImport, interventions) |
| `app/tenant/policy.py` | Tenant policy guards (family comparison mode, attendance counting, cohort chart gating) |
| `app/routers/interventions.py` | Intervention case workflow API + admin UI when `features.interventions` is enabled |
| `app/import_/` | CSV validate/apply CLI (`python -m app.import_.cli validate|apply`) — underscore because `import` is reserved |
| `app/import_/history.py` | Import run audit log (`import_runs` table) |
| `app/audit.py` | Append-only security audit events (`security_audit_events` table) |
| `app/routers/students.py` | Admin student search (`GET /api/students/search`) |
| `app/routers/remarks.py` | Teacher remarks: `POST /api/remarks` (draft or `publish`), `POST /api/remarks/{id}/publish`; family charts see published only |
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

### Local Kubernetes (kind)

Optional cluster parity before cloud deploy: [kind-helm-local.md](operations/kind-helm-local.md). GitHub **deploy** / **qa-regression** for staging and production require `KUBECONFIG` on the matching GitHub environment — see [stg-regression-signoff.md](operations/stg-regression-signoff.md) and [prod-deploy-runbook.md](operations/prod-deploy-runbook.md). Ephemeral ops gates are summarized in `TASK_HANDOFF.md` at the repo root (not duplicated here).

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

- Out-of-scope IDs and charts a role may not see return **404**, not 403 (config-repo QA regression enforces this for charts).
- Portal routes use **403** when the signed-in role opens another role's HTML portal — that is intentional.
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

~640 tests including tenant isolation, authz, charts, insights, import validation, ERP webhook/connectors/conflicts, interventions, policy, and QA contract tests (`tests/test_qa_contract.py`). Confirm with `PYTHONPATH=. pytest -q --collect-only`.

Always pass `Host: sunrise.localhost` (or `horizon.localhost`) in API tests — see `tests/conftest.py` `DEFAULT_HEADERS`.

---

## Deployment artifacts

| Path | Purpose |
| --- | --- |
| `deploy/Dockerfile` | Production image |
| `deploy/docker-compose.yml` | Local Postgres + app |
| `deploy/helm/school-analytics/` | Shared K8s release |
| `.github/workflows/preflight.yml` | CI: ruff, pytest, docs drift, helm lint |
| `.github/workflows/supply-chain.yml` | CI: pip-audit, lock drift, CycloneDX SBOM |
| `.github/workflows/release.yml` | Image publish on `v*` tag |
| `requirements.lock` | Pinned transitive deps (`pip-compile`); used in Docker + CI |
| `docs/operations/supply-chain.md` | Dependency locking, vulnerability scan, SBOM |
| `docs/operations/production-support-matrix.md` | Supported versions, MFA, RPO/RTO, exclusions |

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

**Last updated:** 2026-11-09 (v1.3.0 ERP connector slice; handoff reconciliation — Alembic head, test count, Phase 2 / ops pointers).

---

## P0 §12.1 checklist (audit handoff)

Tracked against authoritative `school_platform_ai_handoff.md` §12.1. **Do not begin new post-P0 feature work until all items marked Pass** (or product owner explicitly waives a Partial).

| # | Requirement | Status |
| --- | --- | --- |
| 1 | Attendance-to-term derivation + backfill | **Pass** |
| 2 | Reject marks outside bounds / absent-state rules | **Pass** |
| 3 | Real baseline migration | **Pass** |
| 4 | Remove production startup DDL; migration job | **Pass** |
| 5 | PostgreSQL migration + RLS integration tests | **Pass** (`postgres-integration.yml`) |
| 6 | MFA enforced for privileged roles | **Pass** |
| 7 | Tenant-operator DB binding + cross-DB tests | **Pass** |
| 8 | ERP webhook secret, signing, replay protection | **Pass** |
| 9 | Unified app/config schema + release contract | **Pass** — `tenant_config/models.py` ↔ `tenant.schema.json`; `webhookSecretRef` not `webhookToken`; prod admin MFA enforced in schema + operator |
| 10 | Live regression tests match auth behavior | **Pass** — `tests/test_qa_contract.py` mirrors `qa/regression.py` (`ROLE_FORBIDDEN_CHART`, `/api/auth/login`, `/healthz`, `/readyz`) |
| 11 | Exact section+subject remark authorization | **Pass** |
| 12 | Secure cookies + stable `SECRET_KEY` in production | **Pass** |
| 13 | CSRF + POST-only logout | **Pass** |
| 14 | Shared login throttling + trusted-proxy config | **Pass** — `RedisLoginThrottleStore` when `LOGIN_THROTTLE_BACKEND=redis`; `TrustedProxyMiddleware` + `TRUSTED_PROXY` |
| 15 | Invitation, forced password change, reset, recovery | **Pass** — `POST /api/admin/users/invite` (temp password + activation token, `must_change_password`); reset + forced change done |
| 16 | Security + data-access audit events | **Pass** — CSV export, bulk import apply, sensitive chart access audited |
| 17 | Separate readiness/liveness; fail on DB error | **Pass** |
| 18 | Define and validate deployment secrets | **Pass** — `scripts/validate_deploy_secrets.py` checks `database-url`, `database-migrate-url`, `secret-key` |
| 19 | Backup, restore, recovery tests | **Pass** |
| 20 | Fix or remove dead platform settings route | **Pass** (`/admin/platform`) |
| 21 | Feature flags control navigation consistently | **Pass** (`app/portals.py`) |
| 22 | Lock dependencies + vulnerability/SBOM controls | **Pass** — `requirements.lock`, `supply-chain.yml`, `docs/operations/supply-chain.md` |
| 23 | Remove/pin `latest` from release workflows | **Pass** |
| 24 | Kubernetes security context + hardening | **Pass** — `runAsNonRoot`, dropped caps, seccomp, `readOnlyRootFilesystem` + `/tmp` emptyDir, optional PDB |
| 25 | Production support matrix + runbook | **Pass** — `docs/operations/production-support-matrix.md`, `backup-restore.md` |

**Post-P0 gate:** All §12.1 items **Pass**. Per handoff §41 step 24, **`InterventionCase` new work is cleared** for teams treating strict P0 exit literally.

---

## P0-C progress (audit handoff §26.3)

Tracked against the authoritative `school_platform_ai_handoff.md`; do not treat this as a replacement.

| Item | Status |
| --- | --- |
| TOTP MFA (privileged roles) | **Done** — enrol/verify, encrypted secrets, rate-limited verify |
| CSRF on state-changing requests | **Done** |
| Password reset (admin + self-service token) | **Done** — `password_reset_tokens`, single-use + expiry, rate-limited forgot |
| Session revocation (`session_version` in cookie) | **Done** — bumped on password change/reset; stale cookies rejected |
| Forced first-password change (`must_change_password`) | **Done** — set on CSV import; middleware blocks portal/API until changed |
| Login throttling (shared backend) | **Done** — `RedisLoginThrottleStore` + `TRUSTED_PROXY` / `TrustedProxyMiddleware` |
| Invite/activation token | **Done** — `POST /api/admin/users/invite` |
| MFA recovery codes / re-enrollment states | **Done** — hashed one-time codes; `POST /api/auth/mfa/recovery-codes` (re-auth); `POST /api/auth/mfa/recovery` and `POST /login/mfa/recovery`; admin reset at `POST /api/admin/users/{id}/mfa/reset` with session revoke + audit |
| Session/device list UI | **Not started** |
| SSO / verified contact workflows | **Not started** |

**Alembic head (current):** `016_import_conflicts`. **Historical note:** security audit and account lifecycle landed in `008` / `009_security_audit_events` (see P0 auditability below); later Phase 2 revisions include `015_import_sync_state` (delta sync) and `016_import_conflicts`.

### P0 auditability (handoff §24.4)

| Item | Status |
| --- | --- |
| `security_audit_events` table + tenant RLS | **Done** — Alembic `009_security_audit_events` |
| `record_security_event()` helper | **Done** — `app/audit.py`; metadata sanitized (no passwords/tokens) |
| Login success/failure, logout, password reset, MFA verify | **Done** — wired in `app/routers/auth.py` |
| Admin password-reset API audit | **Done** |
| Session revocation bump metadata | **Done** — `session.revoked` in `set_user_password()` |
| Data-access audit events (imports, exports, chart access) | **Done** — export CSV, import apply, sensitive charts |
| Audit explorer UI | **Not started** (P1) |

---

## Authentication

- **Browser:** `GET/POST /login` — form-based sign-in. When tenant MFA applies, password success shows a second step for the TOTP code (or enrolment when required and not yet set up).
- **Automation / QA:** `POST /api/auth/login` with JSON `{"email","password"}` — response includes `userId`, `role`, `mfaPending`, `mfaEnrolmentNeeded`, and when applicable `mfaToken` or `enrolmentToken`. Complete MFA with `POST /api/auth/mfa/verify` (`{"code","mfaToken"}`). Enrol with `POST /api/auth/mfa/enroll/start` and `POST /api/auth/mfa/enroll/confirm`.
- **MFA:** TOTP (RFC 6238) via `pyotp`. Secrets are Fernet-encrypted at rest using a key derived from `SECRET_KEY`. Tenant `auth.mfaEnabled` plus `auth.mfaRequiredRoles` gate which roles must enrol and verify on each sign-in. Short-lived signed cookies/tokens (`sa_mfa_pending`, `sa_mfa_enrol`, 5 min) bridge password success to MFA verify/enrol; full session cookie is issued only after TOTP passes. MFA verify attempts are rate-limited like password login. QA regression still treats unexpected `mfaPending: true` as failure on tenants that have MFA disabled.
- **Password reset:** `GET/POST /forgot-password` (generic response, rate-limited); `GET/POST /reset-password?token=…` (single-use SHA-256 hashed tokens in `password_reset_tokens`, default 1 h TTL). Admin: `POST /api/admin/users/{user_id}/password-reset` returns `resetToken` once (no email delivery in v1).
- **Forced password change:** `users.must_change_password` set on CSV import temp passwords; middleware redirects to `GET/POST /change-password` until cleared. Session cookie includes `sv` (`session_version`); password change/reset increments it and invalidates older cookies. Login throttle: `app/throttle.py` (`LOGIN_THROTTLE_BACKEND=memory` default; `redis` when `LOGIN_THROTTLE_REDIS_URL` set). Trusted proxy: `TRUSTED_PROXY=true` enables `X-Forwarded-For` via `TrustedProxyMiddleware`.
- **Admin invite:** `POST /api/admin/users/invite` creates a user with a one-time `tempPassword`, `activationToken` (password-reset flow), and `must_change_password=true`.
- **Welcome (F04):** First portal visit per browser may show an optional role-aware `<dialog>` (`app/welcome.py`, `welcome.js`). Dismiss via **Skip** → `POST /api/welcome/dismiss` sets signed cookie `sa_welcome_v1`. Re-open from Help → **Portal tour** (`GET /help/welcome` → home `?welcome=1`). Suppressed during forced password change / MFA enrolment. Respects `prefers-reduced-motion`; mobile uses bottom-sheet CSS at 640px.

---

## Health probes

| Path | Purpose |
| --- | --- |
| `/healthz` | **Liveness** — process up; no dependency checks. K8s liveness + Docker `HEALTHCHECK`. |
| `/readyz` | **Readiness** — verifies database connectivity and that Alembic is at head; returns 503 when DB is unavailable or migrations are pending. K8s readiness probe. Response includes `database` and `migrations` (`ok` / `pending` / `unavailable`). |

Both paths bypass tenant hostname resolution (`TenantMiddleware` exempt list).

---

## Metric glossary (F01)

Canonical catalogue: `app/analytics/metric_registry.py` (generated `docs/generated/metrics_registry.{json,yaml}`). Role-aware API:

| Path | Purpose |
| --- | --- |
| `GET /api/metrics` | List metrics visible to the signed-in role; optional `?q=` search |
| `GET /api/metrics/{metric_id}` | One entry; **404** if out of audience or `family_safe=False` for parent/student |
| `GET /help/metrics` | HTML appendix (authenticated); linked from all portal sidebars (F01-04) |
| `GET /api/charts/{key}/info` | Chart-level metric definitions for the **About this chart** panel (F01-04) |

Staff-only fields (`implementation` pointers) are omitted for parent/student. Formula regression: `tests/test_metric_formulas.py`; API: `tests/test_metrics_glossary.py`.

---

## Phase 2

Scope and roadmap: [docs/product/phase2.md](product/phase2.md) and [docs/product/erp-strategy.md](product/erp-strategy.md). **Release:** [v1.3.0](releases/v1.3.0.md) (2026-11-09) — ERP ingest, connector sync, delta sync, import conflicts.

**Shipped (through v1.3.0):**

- `import_runs` audit table (Alembic `004_import_runs`) — records validate/apply from CLI, `/admin/import`, `/api/import/*`, and `/api/erp/import`.
- Admin **Data import** page lists recent runs (status, source, files, error count).
- Admin filter bar: student **combobox** (focus opens roster browse via `GET /api/students/search?q=`, typing filters); **subject** and **class** are single-select dropdowns (`subject_id`, `section_id`).
- **Import apply** — `school_structure.csv` (grades/sections/subjects), score upsert by `assessment_code` → `assessments.external_id`, extract-error guards, 5 MB per-file / 25 MB bundle / 50 MB zip decompressed caps, temp dir cleanup.
- **Parent-safe insights** — `student.insight_summary` chart on parent/student overview (no class averages or staff actions); admin keeps `student.insights`.
- **ERP webhook** — `POST /api/erp/import` with HMAC-signed requests when `bulkImport` is enabled. Tenant config stores only `erp.webhookSecretRef` (and optional `erp.webhookSigningSecretRef`); runtime resolves `SA_SECRET_<REF>` from deployment secrets. Missing ref or unresolved secret fails closed (401 at request time; startup abort in production).
- **Intervention workflow** — `intervention_cases` / `intervention_contacts` tables, admin UI at `/admin/interventions`, API at `/api/interventions` when `features.interventions` is enabled.
- **Policy layer** — `app/tenant/policy.py` applies `portal.familyComparisonMode` and attendance counting rules from tenant settings.

- **Delta sync** — bundle SHA-256 fingerprint per tenant `sync:*` channel (`import_sync_state`, Alembic `015`); skipped runs use `import_runs.status=skipped`; CLI `--force`. See [erp-delta-sync.md](operations/erp-delta-sync.md).
- **Import conflicts** — `import_conflicts` (Alembic `016`), `GET/POST /api/import/conflicts`, admin **Data import** UI (keep local / accept ERP / per-field). Smoke: [erp-import-conflicts-smoke.md](operations/erp-import-conflicts-smoke.md).
- **Connector sync** — scheduled pull via `python -m app.import_.connectors.cli sync` (`local` drop dir, `sftp`, `rest` polling). Runbook: [erp-connector-sync.md](operations/erp-connector-sync.md); webhook smoke: [erp-webhook-smoke.md](operations/erp-webhook-smoke.md).

**Ops (deploy, not product scope):** local cluster — [kind-helm-local.md](operations/kind-helm-local.md); staging sign-off — [stg-regression-signoff.md](operations/stg-regression-signoff.md); production — [prod-deploy-runbook.md](operations/prod-deploy-runbook.md).

**Deferred (product):** Full bidirectional ERP sync and expanded conflict preview UX remain out of scope for the v1.3.0 slice per `TASK_HANDOFF.md` §6 and [phase2.md](product/phase2.md). Shipped work is ingest paths (CLI, admin, webhook, connectors), delta sync, and conflicts UI/API.

---

## Filter bar (admin UX)

- **Student:** combobox on admin pages — click/focus opens browse list (`q=` empty, up to 50 rows); typing filters by name/admission/email prefix; selection navigates to `/admin/student/{id}` on lookup/drill-down routes. Dropdown stacks above chart cards (`.filters` z-index + `.is-open` on combobox). **Student lookup** (`/admin/student`) sets `require_student`: charts stay empty until a student is picked (no roster default on that page).
- **Subject / class:** single-select dropdowns for all roles (`subject_id`, `section_id`). A selected class highlights that row on the class leaderboard and keeps every in-scope class visible on the class comparison chart.
- **Multi-select via API:** repeated `subject_ids` / `section_ids` query params still work for bookmarks, exports, and integrations; `_normalize_filter_controls()` maps a lone list value onto the singular dropdown field when rendering a page.
- Non-admin roles use the same singular dropdown fields (no combobox on student picker except admin).

---
