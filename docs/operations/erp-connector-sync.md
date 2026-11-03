# ERP connector sync (scheduled ingest)

Phase 2 adds a **connector framework** beside webhook ingest (`POST /api/erp/import`). Connectors fetch CSV bundles from an ERP export channel, then reuse the same validate/apply pipeline as CLI and admin upload.

## Supported today

| Kind | Status | Import run `source` |
| --- | --- | --- |
| `local` | **Supported** — read a mounted directory | `sync:local` |
| `sftp` | Stub (raises at fetch time) | `sync:sftp` |
| `rest` | Stub (raises at fetch time) | `sync:rest` |

Webhook ingest remains the preferred push path when the ERP can POST signed multipart uploads.

## Local drop directory (CronJob pattern)

1. Mount an SFTP sidecar or ERP export volume at a fixed path (for example `/var/erp-drop/sunrise`).
2. Ensure the bundle contains recognized CSV files (`school_structure.csv`, `scores.csv`, etc.).
3. Run sync as the migration/job service account:

```bash
PYTHONPATH=. python -m app.import_.connectors.cli sync \
  --tenant-id=<tenant-uuid> \
  --kind=local \
  --dir=/var/erp-drop/sunrise
```

Exit code `0` when apply succeeds; `1` on validation/apply failure; `2` on connector configuration errors.

Recent runs appear on **Admin → Data import** with source `sync:local`.

## Planned: SFTP and REST

Future slices will resolve host/path or base URL from deployment secret refs (same `SA_SECRET_<REF>` pattern as `erp.webhookSecretRef`). Until then, use webhook push or a local drop directory plus the CLI above.

## Related

- [ERP webhook smoke](./erp-webhook-smoke.md)
- [ERP strategy](../product/erp-strategy.md)
- [Phase 2 scope](../product/phase2.md)
