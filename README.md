# School Analytics Platform

Parents cannot usually see how their child is doing until a report card arrives, and
schools rarely have classroom analytics at all. This is one application with four
portals, each showing the analytics that stakeholder actually needs, over a full
academic year of assessment data.

| Portal | Who signs in | What they get |
| --- | --- | --- |
| Parent | A guardian | Their child's score trend across the whole year, per subject, against the class average, and where the child is improving or slipping |
| Student | A student | Their own performance, subject strengths, term-over-term progress |
| Teacher | A subject or class teacher | Classroom analytics for the sections and subjects they teach: distribution, at-risk students, per-assessment difficulty |
| Principal | School leadership | School-wide view: grade-wise and subject-wise performance, class comparison, term trends, at-risk cohort, teacher effectiveness |

## Stack

Pure Python, no Node and no build step.

- FastAPI + Uvicorn, SQLAlchemy 2.x over SQLite, Pydantic v2 for every query parameter
- Jinja2 server-rendered pages with autoescaping, Chart.js from a CDN
- Session cookie auth with bcrypt password hashing and signed, expiring tokens
- pytest for the analytics maths and the authorization guards

## Multi-tenant platform

This application serves many schools from one deployment. Each school is a **tenant** resolved by hostname (for example `sunrise.localhost` after seeding). Tenant business settings live in the separate **school-analytics-config** repository and are applied by the Python tenant operator:

```bash
# Clone the config repo beside this project (separate repository)
git clone https://github.com/Apoorv-A/school-analytics-config.git

PYTHONPATH=. python -m tenant_operator validate --config=../school-analytics-config/tenants/sunrise/dev.yaml
PYTHONPATH=. python -m tenant_operator apply --config=../school-analytics-config/tenants/sunrise/dev.yaml
```

Documentation lives under [`docs/`](docs/README.md). For AI handoff and full platform context, see [`docs/AI_HANDOFF.md`](docs/AI_HANDOFF.md).

**Config repository (separate):** https://github.com/Apoorv-A/school-analytics-config

```bash
python scripts/docs_generate.py
python scripts/docs_verify.py
```

Local Docker stack (PostgreSQL):

```bash
docker compose -f deploy/docker-compose.yml up --build
```

## Run it (local demo)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set SECRET_KEY. Generate one with:
python -c "import secrets; print(secrets.token_urlsafe(48))"
# For a local demo also set DEMO_MODE=true and DEMO_PASSWORD to the seed password.

python -m seed.generate          # add --reset to rebuild an existing database
uvicorn app.main:app --reload
```

Use `--host 0.0.0.0 --port 8000`. On macOS, `sunrise.localhost` and `horizon.localhost` usually resolve without `/etc/hosts`; add `127.0.0.1 sunrise.localhost horizon.localhost` only if the browser cannot reach them.

Open http://sunrise.localhost:8000 and sign in. The seeder prints the demo logins when it
finishes; with `DEMO_MODE=true` the login page also lists them and fills them in on
click. Default seed password is `Demo@12345` when `DEMO_PASSWORD` is set to that value before seeding.

| Role | Email |
| --- | --- |
| Principal | `principal@sunrise.edu` |
| Teacher | `sunita.deshpande@sunrise.edu` |
| Parent | `ananya.rao@parent.sunrise.edu` |
| Student | `ananya.rao@student.sunrise.edu` |

Second tenant (isolation demo): http://horizon.localhost:8000 — Horizon admin also uses `principal@sunrise.edu` (same email, different tenant; proves login is hostname-scoped).

The password for every seeded account is whatever `DEMO_PASSWORD` was set to when
the database was seeded. These accounts exist only for synthetic data.

## Seeded data

Two demo tenants after `seed.generate --reset`:

| Tenant | Hostname | Data |
| --- | --- | --- |
| **Sunrise Academy** | `sunrise.localhost` | Full school: grades 6–10, two classes each, 150 students, 12 teachers, 8 subjects, 3 terms, ~1,120 assessments and 16,800 scores, attendance and remarks |
| **Horizon Academy** | `horizon.localhost` | Minimal second tenant to prove isolation and per-tenant login (overlapping admin email, separate data) |

Every Sunrise student has a latent ability, per-subject aptitudes, and a
trajectory (steady, improving, declining, struggling), so the trend, consistency,
and at-risk analytics have something real to find.

Assessment types span unit test, mid-term, final, assignment, and project, which is
what makes "throughout the year" a genuine timeline rather than two data points.

## How it is put together

```
app/
  main.py            app wiring, security headers, error pages
  config.py          settings from the environment
  db.py              SQLAlchemy engine (SQLite NullPool locally, Postgres pool in prod)
  models.py          tenants, domains, academic years, terms, grades, sections,
                     subjects, users, students, teachers, assignments, assessments,
                     scores, attendance, remarks
  tenant/            hostname middleware, context, RLS, per-tenant settings
  schemas.py         validated filter parameters
  security.py        bcrypt hashing, signed session tokens
  deps.py            AccessScope: the row-level authorization guard
  portals.py         navigation and shared dashboard rendering
  analytics/
    metrics.py       pure functions: percentages, letter grades, rank, percentile,
                     trend slope, consistency, at-risk flags, distributions
    queries.py       scoped SQL aggregation over the score facts
    charts.py        the chart registry: one handler per visualization
  routers/           auth, parent, student, teacher, admin, chart API, CSV export
  templates/         Jinja2 pages and card components
  static/            design system CSS, filter bar, Chart.js helpers
tenant_config/       Pydantic models for tenant YAML (schema source)
tenant_operator/     CLI: validate, apply, schema export
seed/generate.py     synthetic school generator (sunrise + horizon tenants)
docs/                product, architecture, ADRs, generated catalogs
deploy/              Dockerfile, docker-compose, Helm chart
tests/               metrics, authorization, tenant isolation, chart coverage
```

Every visualization is a JSON endpoint under `/api/charts/<key>`, so the filter bar
re-renders charts without a page reload and the same metric code serves all four
portals. `/export/<key>.csv` returns the same data as CSV, and every page is styled
for printing to PDF.

### Filters

Academic year, term, subject, grade, class, student, assessment type, and a date
range. Each page enables only the filters it is allowed to use, and the active
filters are kept in the URL so a view can be shared or reloaded.

## Authorization

Access is decided in one place, `AccessScope` in `app/deps.py`, and threaded through
every query rather than being checked in templates:

- a parent resolves only to their own children
- a teacher only to the classes and subjects they are assigned
- a student only to themselves
- a principal is school-wide

Out-of-scope identifiers return 404 rather than 403, so the response does not confirm
that a record exists. A parent still needs their child's class average and rank,
which cannot be derived from one child's marks, so `cohort_view()` opens the child's
classroom for aggregates only and any query that would name a classmate refuses to
run against it.

## Tests

```bash
pytest            # 294 tests
ruff check app seed tests
```

The suite covers the analytics maths (including None handling and boundaries), the
authorization guards (a parent cannot read another child, a teacher cannot read an
unassigned class, a student cannot read a classmate, tampered cookies are rejected),
every chart for every role, filter application, CSV exports, and output escaping.
