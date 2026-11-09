# ERP delta sync (bundle fingerprint)

Scheduled connector sync (`sync:local`, `sync:sftp`, `sync:rest`) compares each fetched CSV bundle to the **last successfully applied** bundle for that tenant and channel. When the content fingerprint is unchanged, validate/apply is skipped and an `import_runs` row is recorded with status **`skipped`**.

Push ingest (`POST /api/erp/import`, admin upload, CLI apply) is **not** skipped — operators and ERP webhooks may intentionally resend the same export.

## How fingerprinting works

- Only recognized bundle files participate (`school_structure.csv`, `users.csv`, `scores.csv`, `attendance.csv`, `remarks.csv`).
- Files are hashed in sorted filename order; missing files are omitted (same rules as validate/apply).
- Fingerprint algorithm: SHA-256 hex digest (see `app/import_/bundle_fingerprint.py`).
- State is stored per `(tenant_id, source)` in `import_sync_state` (Alembic `015_import_sync_state`).

## Operator flags

| Flag | Behavior |
| --- | --- |
| *(default)* | Skip apply when fingerprint matches last success for this `sync:*` source |
| `--force` | Always validate/apply; refresh stored fingerprint on success |

```bash
PYTHONPATH=. python -m app.import_.connectors.cli sync \
  --tenant-id="${SUNRISE_TENANT_ID}" \
  --kind=local \
  --dir="$(pwd)/docs/onboarding/csv-templates" \
  --force
```

Exit code `0` when apply succeeds **or** when the run is skipped as unchanged; `1` on validation/apply failure.

## Audit trail

Skipped runs appear on **Admin → Data import** with status `skipped` and source `sync:local` / `sync:sftp` / `sync:rest`. JSON report includes `skipped: true` and `delta.reason: bundle_unchanged`.

## Import conflicts (row-level)

When a changed bundle is applied via **`sync:*`** or **`POST /api/erp/import`**, rows marked analytics-owned (`source_system=analytics`, e.g. teacher remarks or locally adjusted scores) are **not** overwritten silently. A pending row is stored in **`import_conflicts`** and surfaced on **Admin → Data import**.

Principal CLI/upload apply (`source=cli|upload`) still overwrites — operators explicitly chose that bundle.

Resolution strategies: **keep local**, **accept ERP**, or **per-field** merge on the conflicting columns. See [Phase 2 scope](../product/phase2.md).

**Local smoke:** [ERP import conflicts walkthrough](./erp-import-conflicts-smoke.md).

## Related

- [ERP connector sync](./erp-connector-sync.md)
- [Phase 2 scope](../product/phase2.md)
