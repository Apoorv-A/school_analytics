# ERP coexistence strategy

School Analytics is **read-mostly**. The school's ERP remains the system of record for admissions, fees, timetables, and day-to-day mark entry in Phase 1.

## Phase 1 (current)

| Channel | Role | Data |
| --- | --- | --- |
| ERP export / manual CSV | Operator or principal | Structure, users, scores, attendance, remarks |
| `app.import_` CLI + admin upload | Principal (when `bulkImport` enabled) | Validated ingest into tenant DB |
| Analytics portals | All roles | Charts, insights, exports |

Correlation fields `external_id` and `source_system` on core entities allow idempotent upserts and future connector matching.

Teachers may add single-student remarks via `POST /api/remarks` (scoped to assigned students).

## Phase 2

- Scheduled ERP sync (SFTP, webhook, or REST connectors)
- Delta sync with conflict resolution UI
- Optional parent-safe insight summaries

## Phase 3 (optional ERP-lite)

Modular add-ons (fees, admissions, timetable) behind feature flags only when a school **replaces** its ERP. Analytics core unchanged.
