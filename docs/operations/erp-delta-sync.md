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

## Next: conflict resolution UI

This slice avoids row-level merge logic. The upcoming conflict UI will build on:

- **`import_sync_state`** — knowing when the ERP export last changed.
- **`import_runs`** history — correlating skipped vs applied sync cycles.
- **External correlation ids** on students, assessments, remarks (`external_id`, `source_system`) — detecting per-entity changes inside a changed bundle.
- **Idempotent apply** — today’s upserts define the baseline “winning” row without operator choice; conflicts will surface when incoming ERP rows disagree with analytics-owned edits.

## Related

- [ERP connector sync](./erp-connector-sync.md)
- [Phase 2 scope](../product/phase2.md)
