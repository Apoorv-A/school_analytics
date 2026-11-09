# ERP import conflicts — local smoke walkthrough

Use this after Phase 2 conflict resolution is deployed (`import_conflicts` table, Alembic `016_import_conflicts`) and the tenant has `features.bulkImport: true` (see [school-analytics-config](https://github.com/Apoorv-A/school-analytics-config)).

Conflicts appear only when **ERP-scheduled paths** (`sync:*`, `webhook`) try to change rows the school already owns in analytics (`source_system=analytics`). **Admin CSV upload / CLI apply** (`source=upload|cli`) still overwrites on purpose.

## What you are proving

1. A second ERP ingest queues a row in `import_conflicts` instead of silently overwriting local marks or remark text.
2. **Admin → Data import** lists pending conflicts with actions.
3. `GET /api/import/conflicts` and `POST /api/import/conflicts/{id}/resolve` apply keep local, accept ERP, or per-field choices and write a security audit event.

## Preconditions

| Item | Notes |
| --- | --- |
| Migrations | `alembic upgrade head` (through `016_import_conflicts`) |
| Seed / data | `python -m seed.generate --reset` (or existing Sunrise demo DB) |
| Tenant features | `bulkImport: true` on the tenant (`settings_json` or config operator apply) |
| Login | Principal/admin at `http://sunrise.localhost:8000` (`Demo@12345` when `DEMO_MODE` matches seed) |
| Host header | Browser uses `sunrise.localhost`; API tests use `Host: sunrise.localhost` |

## Path A — Admin UI (fastest)

### 1. Seed a score from ERP, then edit locally

From the app repo, with venv active and DB migrated:

```bash
# Optional: use a throwaway CSV dir
mkdir -p /tmp/conflict-smoke
```

Build a minimal `scores.csv` for one known student (pick admission number from Admin → Students or seed roster). Example shape:

```text
admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system
<ADMISSION>,UT-MATH-6A-T1,72,false,score-smoke-1,erp-v1
```

Apply once as **webhook** (queues conflicts on the *next* ERP apply, not this one):

```bash
TENANT_ID=$(sqlite3 school.db "select id from tenants where key='sunrise';")
PYTHONPATH=. python -c "
from pathlib import Path
from app.db import SessionLocal
from app.import_.apply import apply_directory
import uuid
tid = uuid.UUID('$TENANT_ID')
with SessionLocal() as db:
    print(apply_directory(db, tid, Path('/tmp/conflict-smoke'), source='webhook'))
    db.commit()
"
```

Mark the row **analytics-owned** with a local edit (SQL or teacher workflow). Quick SQL example:

```bash
sqlite3 school.db "update scores set marks_obtained=85, source_system='analytics' where external_id='score-smoke-1';"
```

### 2. Re-apply the same bundle with a changed ERP mark

Change `marks_obtained` in the CSV (e.g. `90`) and `source_system` to `erp-v2`, then run the same `apply_directory(..., source='webhook')` snippet. Expect `conflicts_queued: 1` in the printed report; the live score should **stay 85**.

### 3. Resolve in the browser

1. Open **Admin → Data import** (`/admin/import`).
2. Confirm the **Import conflicts** table shows the pending row (entity, fields, local vs ERP values).
3. Try each resolution:
   - **Keep local** — score remains `85`.
   - **Accept ERP** — score becomes ERP value (`90`).
   - **Use ERP `<field>`** / **Apply field picks** — per-field merge when multiple columns differ (attendance rows may expose more than one field).

Refresh the page; resolved rows leave the pending list.

## Path B — API only (automation / curl)

After step 2 above, list conflicts:

```bash
curl -s -b cookies.txt -H 'Host: sunrise.localhost' \
  'http://127.0.0.1:8000/api/import/conflicts?status=pending' | python -m json.tool
```

Resolve (accept ERP):

```bash
CONFLICT_ID=1   # from list response
curl -s -b cookies.txt -H 'Host: sunrise.localhost' -H 'Content-Type: application/json' \
  -X POST "http://127.0.0.1:8000/api/import/conflicts/${CONFLICT_ID}/resolve" \
  -d '{"strategy":"accept_erp"}' | python -m json.tool
```

Per-field example:

```bash
curl -s -b cookies.txt -H 'Host: sunrise.localhost' -H 'Content-Type: application/json' \
  -X POST "http://127.0.0.1:8000/api/import/conflicts/${CONFLICT_ID}/resolve" \
  -d '{"strategy":"per_field","field_choices":{"marks_obtained":"incoming"}}' | python -m json.tool
```

Sign in first via `POST /api/auth/login` or the login form and store the session cookie in `cookies.txt`.

## Path C — pytest reference

CI encodes the same rules:

- `tests/test_import_conflicts.py` — detection for webhook/sync vs upload overwrite, remark conflicts, API resolve + audit.
- `tests/test_erp_webhook.py` — signed ingest still respects conflict queueing.

```bash
PYTHONPATH=. pytest -q tests/test_import_conflicts.py
```

## Negative checks

| Action | Expected |
| --- | --- |
| Admin upload same changed CSV (`source=upload`) | **No** conflict row; ERP value wins |
| Resolve same conflict twice | HTTP **409** |
| Parent/teacher calls conflict API | **404** (admin + `bulkImport` only) |

## Related

- [ERP delta sync](./erp-delta-sync.md) — fingerprint skip + conflict overview
- [ERP webhook smoke](./erp-webhook-smoke.md) — signed `POST /api/erp/import`
- [Phase 2 scope](../product/phase2.md) — deferred: full bidirectional sync and validate-only diff preview
