# Phase 2 scope

Phase 2 extends ingest and insight delivery while keeping analytics read-mostly. See [erp-strategy.md](erp-strategy.md) for the full ERP coexistence plan.

## Planned themes

| Theme | Goal |
| --- | --- |
| ERP sync | Scheduled connectors (SFTP, webhook, REST), delta sync, conflict UI |
| Import operations | Audit trail, run history, operator visibility |
| Parent-safe insights | Sanitized insight summaries for parent portal |
| Analytics depth | Additional cohort views and export improvements |

## Delivered in this slice

- **`import_runs` table** — tenant-scoped audit log for validate/apply (CLI, admin upload, API, webhook).
- **Admin import page** — shows recent import activity after uploads.
- **Filter bar UX (admin)** — student combobox (click-to-browse + type-to-filter), subject/class multi-select with all options visible plus list panel.
- **Import apply fixes** — school structure, score upsert, extract-error guards, upload/zip size caps, temp dir cleanup.
- **Parent-safe `student.insight_summary`** — guardian/student portal card without class benchmarks, remark bodies, or staff actions.
- **ERP webhook ingest** — `POST /api/erp/import` with HMAC-signed requests when `features.bulkImport` is enabled. Tenant config stores `erp.webhookSecretRef` (and optional `erp.webhookSigningSecretRef`); runtime resolves `SA_SECRET_<REF>` from deployment secrets.

## Product note: school-provided analytics

Analytics is offered to families as a **school service** under the school’s existing enrollment and privacy notices. The platform does **not** collect separate in-app parental consent for viewing analytics; legal basis and notices are between the school and families (platform acts as processor where applicable).

## Delivered (operations)

- **ERP webhook smoke** — `docs/operations/erp-webhook-smoke.md` and `scripts/erp_webhook_smoke.py` for signed `POST /api/erp/import` against local or staging hosts.
- **Connector framework (foundation)** — `app/import_/connectors/` with local drop-directory sync, SFTP/REST stubs, and `python -m app.import_.connectors.cli sync` for CronJob-style scheduled ingest. See [erp-connector-sync.md](../operations/erp-connector-sync.md).

## Next slices (not yet built)

- SFTP and REST polling connector implementations
- Delta detection and conflict resolution UI
