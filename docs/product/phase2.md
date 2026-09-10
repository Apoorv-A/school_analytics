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
- **ERP webhook ingest** — `POST /api/erp/import` with per-tenant bearer token (`settings_json.erp.webhookToken`) for automated CSV bundle delivery.

## Next slices (not yet built)

- Connector framework and scheduled sync jobs (SFTP, REST polling)
- Delta detection and conflict resolution UI
