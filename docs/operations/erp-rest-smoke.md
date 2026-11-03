# ERP REST connector smoke test

Use this to verify `sync --kind=rest` against a **local** export server or a real staging export URL. Production CronJobs use the same CLI with https secrets only.

## When not to use REST locally

If you only need to validate CSV apply logic, use **`--kind=local`** with `docs/onboarding/csv-templates/` — no HTTP server or bearer secrets required. See [erp-connector-sync.md](./erp-connector-sync.md#local-drop-directory-cronjob-pattern).

## Secret refs and env vars

Tenant YAML and job args carry **refs only**. Runtime resolves `SA_SECRET_<REF>` where `<REF>` is the ref uppercased with `/` and `-` mapped to `_`:

| Ref (example) | Environment variable |
| --- | --- |
| `sunrise/erp-export-url` | `SA_SECRET_SUNRISE_ERP_EXPORT_URL` |
| `sunrise/erp-rest-export` | `SA_SECRET_SUNRISE_ERP_REST_EXPORT` |
| `sunrise/erp-rest-token` | `SA_SECRET_SUNRISE_ERP_REST_TOKEN` |

Mounted files under `SA_SECRETS_DIR` use `ref` with `/` → `__` (same as webhook ingest).

## Local export server

From the repo root:

```bash
export DEBUG=true
export REST_SMOKE_BEARER_TOKEN='smoke-bearer-token'
PYTHONPATH=. python scripts/rest_export_smoke_server.py --port=8765
```

In another shell (resolve tenant id against the same `DATABASE_URL` as the app):

```bash
export SA_SECRET_SUNRISE_ERP_EXPORT_URL='http://sunrise.localhost:8765/export.zip'
export SA_SECRET_SUNRISE_ERP_REST_TOKEN='smoke-bearer-token'

SUNRISE_TENANT_ID="$(python3 -c "
import os
from sqlalchemy import create_engine, text
url = os.environ.get('DATABASE_URL', 'sqlite:///./school.db')
with create_engine(url).connect() as conn:
    print(conn.execute(text(
        \"SELECT id FROM tenants WHERE key = 'sunrise'\"
    )).scalar_one())
")"

PYTHONPATH=. python -m app.import_.connectors.cli sync \
  --tenant-id="${SUNRISE_TENANT_ID}" \
  --kind=rest \
  --rest-export-url-secret-ref=sunrise/erp-export-url \
  --rest-bearer-token-secret-ref=sunrise/erp-rest-token
```

Use `http://sunrise.localhost:…` rather than `http://127.0.0.1:…` — the REST connector rejects loopback and private IP hosts even when `DEBUG=true`.

## Placeholder URLs

Documentation examples like `https://your-erp.example/export.zip` or `https://erp.vendor.example.com/...` are illustrative. Unless your resolver returns a reachable host, sync will fail with DNS or connection errors in the JSON report (not a validation failure on the CSV bundle).

## CI reference

Automated coverage: `tests/test_connectors.py` (MockTransport zip download, HTTP status errors, DNS-style connect failures) and `tests/test_rest_smoke_script.py` (export zip builder).
