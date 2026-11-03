# ERP connector sync (scheduled ingest)

Phase 2 adds a **connector framework** beside webhook ingest (`POST /api/erp/import`). Connectors fetch CSV bundles from an ERP export channel, then reuse the same validate/apply pipeline as CLI and admin upload.

## Supported today

| Kind | Status | Import run `source` |
| --- | --- | --- |
| `local` | **Supported** — read a mounted directory | `sync:local` |
| `sftp` | **Supported** — pull `*.csv` from a remote inbox | `sync:sftp` |
| `rest` | Stub (raises at fetch time) | `sync:rest` |

Webhook ingest remains the preferred push path when the ERP can POST signed multipart uploads.

Credentials never live in tenant YAML. The CLI and CronJob pass **secret references** only; runtime resolves values from `SA_SECRET_<REF>` (or mounted files under `SA_SECRETS_DIR`), same pattern as `erp.webhookSecretRef`.

## Local drop directory (CronJob pattern)

1. Mount an SFTP sidecar or ERP export volume at a fixed path (for example `/var/erp-drop/sunrise`).
2. Ensure the bundle contains recognized CSV files (`school_structure.csv`, `scores.csv`, etc.).
3. Run sync as the migration/job service account.

Use a **real tenant UUID** (no angle brackets). In zsh, `<uuid>` is treated as stdin redirection and will fail with `no such file or directory: uuid`.

```bash
# Resolve sunrise.localhost tenant id (same DATABASE_URL as the app; default local sqlite):
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
  --kind=local \
  --dir=/var/erp-drop/sunrise
```

Local smoke test with the sample CSV templates in-repo (path must be absolute):

```bash
PYTHONPATH=. python -m app.import_.connectors.cli sync \
  --tenant-id="${SUNRISE_TENANT_ID}" \
  --kind=local \
  --dir="$(pwd)/docs/onboarding/csv-templates"
```

SQLite one-liner (no Python deps): `sqlite3 school.db "SELECT id FROM tenants WHERE key='sunrise';"`

The tenant registry column is **`key`** (for example `sunrise`, `horizon`), not `slug`. Hyphenated UUIDs from the CLI are fine; SQLite may print ids without dashes.

### Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Unknown tenant` | Placeholder UUID (for example `00000000-0000-0000-0000-000000000001`) or a id from another database | Run the lookup above against the **same** database as the app (`DATABASE_URL` in `.env`, default `sqlite:///./school.db`). Use that printed id for `--tenant-id`. |
| `no such column: slug` | Wrong column name in ad-hoc SQL | Use `key`: `SELECT id, key FROM tenants;` |
| `no such file or directory: uuid` (zsh) | Angle brackets around a placeholder | Omit `<>`; pass a real UUID string. |

Exit code `0` when apply succeeds; `1` on validation/apply failure; `2` on connector configuration errors.

Recent runs appear on **Admin → Data import** with source `sync:local` or `sync:sftp`.

## SFTP pull (CronJob pattern)

1. Provision deployment secrets for host, remote directory, username, and password **or** private key PEM (refs only in Helm/job args).
2. Point the ERP export job at the remote inbox directory (absolute path, no `..` segments).
3. Run sync on a schedule; only regular `*.csv` files in that directory are downloaded to a temp folder and applied.

Example secret refs (names are illustrative — match your K8s secret mapping):

| Ref (example) | `SA_SECRET_*` env | Value |
| --- | --- | --- |
| `sunrise/erp-sftp-host` | `SA_SECRET_SUNRISE_ERP_SFTP_HOST` | `sftp.vendor.example.com:22` |
| `sunrise/erp-sftp-path` | `SA_SECRET_SUNRISE_ERP_SFTP_PATH` | `/exports/sunrise` |
| `sunrise/erp-sftp-user` | `SA_SECRET_SUNRISE_ERP_SFTP_USER` | `erp-sync` |
| `sunrise/erp-sftp-password` | `SA_SECRET_SUNRISE_ERP_SFTP_PASSWORD` | *(password)* |

Password auth:

```bash
PYTHONPATH=. python -m app.import_.connectors.cli sync \
  --tenant-id="${SUNRISE_TENANT_ID}" \
  --kind=sftp \
  --sftp-host-secret-ref=sunrise/erp-sftp-host \
  --sftp-path-secret-ref=sunrise/erp-sftp-path \
  --sftp-user-secret-ref=sunrise/erp-sftp-user \
  --sftp-password-secret-ref=sunrise/erp-sftp-password
```

Private key auth (omit `--sftp-password-secret-ref`):

```bash
PYTHONPATH=. python -m app.import_.connectors.cli sync \
  --tenant-id="${SUNRISE_TENANT_ID}" \
  --kind=sftp \
  --sftp-host-secret-ref=sunrise/erp-sftp-host \
  --sftp-path-secret-ref=sunrise/erp-sftp-path \
  --sftp-user-secret-ref=sunrise/erp-sftp-user \
  --sftp-private-key-secret-ref=sunrise/erp-sftp-key
```

SFTP host keys are verified (`RejectPolicy`); ensure the job image trusts the server host key or use a known-good hostname/IP in the host secret.

## Planned: REST polling

REST export polling will resolve a base URL from a deployment secret ref. Until then, use webhook push, SFTP pull, or a local drop directory.

## Related

- [ERP webhook smoke](./erp-webhook-smoke.md)
- [ERP strategy](../product/erp-strategy.md)
- [Phase 2 scope](../product/phase2.md)
