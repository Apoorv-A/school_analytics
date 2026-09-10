# Task Handoff

## 1. Current objective and acceptance criteria

**Objective:** Phase 1 filter/combobox fixes, Phase 2 slice delivery, and a clean review loop (Bugbot + Security Review) with no remaining medium+ findings.

**Acceptance criteria met:**
- Bugbot final pass: no bugs (`d5fc20ec`)
- Security Review: no medium+ issues (`766d47f6`)
- **354 pytest passed** on a clean test DB
- **Student combobox:** dropdown + search; drill-down preserves `student_id` while typing; filter query preserved on student switch
- **Subject/class filters:** single-select dropdowns (reverted from pill UI per user)
- **Student lookup page:** no default student until selected (`require_student`)
- **Phase 2 slice:** `import_runs` audit, parent `student.insight_summary`, ERP webhook `POST /api/erp/import`, per-tenant `erp.webhookToken`
- **Import pipeline fixes:** scores, `school_structure`, remarks, users linking, per-user passwords on CLI/admin apply

## 2. Important decisions and constraints

- **Two repos:** application code in `school_analytics`; tenant YAML/config in `school-analytics-config` (separate clone).
- **Filter bar UI:** NO pill multi-select for subject/class (user preference). Single-select dropdowns in the filter bar. Multi `subject_ids` / `section_ids` via URL/API passthrough only.
- **ERP webhook auth:** per-tenant token only (`settings_json.erp.webhookToken`); fail-closed; **no** global `ERP_WEBHOOK_TOKEN` fallback.
- **Classroom charts:** `section_id` pins and clears `section_ids`; `_section_filters` honors single `section_ids` from bookmarks.
- **Created-user passwords:** returned only on CLI/upload; stripped from audit payloads.
- **RLS:** `tenants` and `tenant_domains` excluded from PostgreSQL RLS (registry/bootstrap tables).

## 3. Files changed and why

| Area | Why |
| --- | --- |
| `app/import_/` | Audit (`import_runs`), apply fixes (scores, structure, remarks, users), webhook ingest |
| `app/analytics/queries.py`, `charts.py` | Section filter semantics, chart scoping |
| `app/static/js/filters.js` | Student combobox, single-select subject/class, query preservation |
| `app/routers/*` | Student search, ERP import endpoint, student lookup `require_student`, parent insight |
| `alembic/versions/003–005` | Schema through `005_remarks_external` |
| `docs/product/phase2.md`, `docs/AI_HANDOFF.md` | Phase 2 scope and canonical agent context |
| `tests/*` | Filter UX, import, webhook, insight summary coverage |

## 4. Tests or commands run, with results

```bash
alembic upgrade head          # through 005_remarks_external
pytest                        # 354 passed (clean test DB)
ruff check                    # clean
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 5. Known problems or unresolved questions

- **Phase 2 remaining** (see `docs/product/phase2.md`): connector framework (SFTP, REST polling), scheduled sync jobs, delta detection, conflict-resolution UI.
- **Test DB pollution:** stale `tests/test_school.db` (+ `.wal`/`.shm`) can cause flaky failures — remove and re-run pytest.
- **`assessment_code` auto-create:** needs UT-style codes (e.g. `UT-MATH-6A-T1`); verify when seeding or importing new assessments.
- **Webhook testing:** each tenant needs `erp.webhookToken` set in config before `POST /api/erp/import` will succeed.

## 6. Exact next step

1. **Commit and push** local changes (not done in this session).
2. **Continue Phase 2** from `docs/product/phase2.md` (connectors, delta sync UI) **or** follow user direction.
3. For webhook smoke tests: set `erp.webhookToken` per tenant in `school-analytics-config`, then POST a CSV bundle to `/api/erp/import`.

## 7. Anything the next agent must not change

- **Do not** reintroduce pill multi-select for subject/class in the filter bar.
- **Do not** add RLS to `tenants` or `tenant_domains`.
- **Do not** add a global `ERP_WEBHOOK_TOKEN` fallback; keep per-tenant bearer auth only.
- **Do not** return created-user passwords in audit/history responses (CLI/upload only).
- **Do not** merge config into the code repo; keep `school-analytics-config` separate.
