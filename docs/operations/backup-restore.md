# Backup and Restore

Operational runbook for backing up School Analytics data and validating a restore before serving traffic.

## Targets

| Metric | Dev (SQLite) | Production (PostgreSQL) |
| --- | --- | --- |
| **RPO** (max acceptable data loss) | 24 hours | 1 hour (nightly logical backup + WAL archiving when enabled) |
| **RTO** (max time to restore service) | 30 minutes | 4 hours (includes migration verify, restore, validation, DNS cutover) |

These are v1 planning targets. Record actual drill results in `docs/learnings/` after each exercise.

## Prerequisites

- Application stopped or in read-only maintenance mode during restore (avoid split-brain writes).
- Migrations applied with `alembic upgrade head` on the **target** database before starting the app.
- `SECRET_KEY` and tenant config unchanged or rotated deliberately with a documented session invalidation plan.
- Helper scripts (from repo root):

```bash
PYTHONPATH=. python scripts/backup_tenant_data.py --help
PYTHONPATH=. python scripts/validate_restore.py --help
```

## SQLite (local development)

### Backup

1. Stop the app (or accept a crash-consistent copy only for non-critical dev data).
2. Full file copy:

```bash
mkdir -p backups
PYTHONPATH=. python scripts/backup_tenant_data.py --output backups/school-$(date +%Y%m%d).db
```

The helper copies `school.db` and any `-wal` / `-shm` sidecars when present.

3. Optional tenant-scoped JSON export (portability / spot checks):

```bash
PYTHONPATH=. python scripts/backup_tenant_data.py \
  --tenant-key sunrise \
  --output backups/sunrise.json
```

### Restore

1. Stop the app.
2. Replace the database file:

```bash
cp backups/school-YYYYMMDD.db school.db
# Remove stale WAL files if the backup was taken while the app was stopped:
rm -f school.db-wal school.db-shm
```

3. Ensure schema is current:

```bash
alembic upgrade head
```

4. Validate:

```bash
export VALIDATE_RESTORE_PASSWORD='Demo@12345'   # seeded demo password
PYTHONPATH=. python scripts/validate_restore.py \
  --tenant-key sunrise \
  --hostname sunrise.localhost
```

5. Start the app and smoke-test `GET /api/tenant/context` with `Host: sunrise.localhost`.

## PostgreSQL (production)

### Backup

Use the platform's scheduled logical backup (recommended: nightly `pg_dump` in custom format). On-demand:

```bash
export DATABASE_URL='postgresql+psycopg://owner:secret@db.example:5432/school_analytics'
mkdir -p backups
PYTHONPATH=. python scripts/backup_tenant_data.py --output backups/school-$(date +%Y%m%d).dump
```

For tenant portability or support exports:

```bash
PYTHONPATH=. python scripts/backup_tenant_data.py \
  --tenant-key sunrise \
  --output backups/sunrise.json
```

Store backups encrypted at rest (S3 SSE-KMS, GCS CMEK, or equivalent). Restrict access to the database owner role used for `pg_dump`.

### Single-tenant restore validation (full cluster restore)

Production runs all tenants in one database with row-level security (RLS). A **full** restore validates the entire cluster.

1. Provision a throwaway database (never restore over production in place without a maintenance window and rollback plan).
2. Restore the dump as the migration owner role:

```bash
createdb school_analytics_restore
pg_restore --no-owner --no-acl --dbname=school_analytics_restore backups/school-YYYYMMDD.dump
```

3. Confirm Alembic revision:

```bash
DATABASE_URL='postgresql+psycopg://owner:secret@db.example:5432/school_analytics_restore' \
  alembic current
```

Re-run `alembic upgrade head` if the backup predates a release migration.

4. Run automated validation:

```bash
export DATABASE_URL='postgresql+psycopg://owner:secret@db.example:5432/school_analytics_restore'
export VALIDATE_RESTORE_PASSWORD='<known-test-account-password>'
PYTHONPATH=. python scripts/validate_restore.py \
  --tenant-key sunrise \
  --hostname sunrise.example.edu \
  --sample-email admin@sunrise.edu
```

Checks performed:

| Check | Meaning |
| --- | --- |
| `alembic_head` | Schema matches shipped migrations |
| `tenant_domains` | Hostname maps to an active tenant |
| `sample_login` | Password hash verifies for a known account |
| `rls_enabled` | `ROW LEVEL SECURITY` + `FORCE` on tenant-scoped tables |

5. Start the app against the restore DB using the **runtime** (non-owner) role. Confirm `/readyz` reports `migrations: ok` and cross-tenant isolation still holds (`tests/test_postgres_integration.py`).

6. After validation, either promote the restore endpoint or discard the throwaway database.

### Point-in-time recovery

When WAL archiving / PITR is enabled on the managed Postgres service, follow the provider runbook to restore to a timestamp, then repeat steps 3–5 above. Document the chosen recovery point in the incident record.

## Kubernetes / Helm

- Run migrations via the `migration-job` Helm hook before or during deploy; do not rely on app startup DDL on PostgreSQL.
- Take backups from the primary writer, not a long-lagging replica.
- After restore, roll pods so connection pools pick up the new endpoint.

## Evidence for release gate (P0)

Keep for each drill or production restore exercise:

1. Backup timestamp and storage location (redact secrets).
2. `validate_restore.py` output (all checks `OK`).
3. `/readyz` and one authenticated API call per restored tenant.
4. Link to a `docs/learnings/` entry if anything failed or exceeded RTO.

Automated coverage: `tests/test_backup_restore.py` (SQLite, no Docker).
