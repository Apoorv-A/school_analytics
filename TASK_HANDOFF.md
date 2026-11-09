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
- **Phase 2 slice:** `import_runs` audit, parent `student.insight_summary`, ERP webhook `POST /api/erp/import`, per-tenant `erp.webhookSecretRef`
- **Import pipeline fixes:** scores, `school_structure`, remarks, users linking, per-user passwords on CLI/admin apply

## 2. Important decisions and constraints

- **Two repos:** application code in `school_analytics`; tenant YAML/config in `school-analytics-config` (separate clone).
- **Filter bar UI:** NO pill multi-select for subject/class (user preference). Single-select dropdowns in the filter bar. Multi `subject_ids` / `section_ids` via URL/API passthrough only.
- **ERP webhook auth:** per-tenant secret ref (`erp.webhookSecretRef` / `erp.webhookSigningSecretRef` → `SA_SECRET_<REF>`); fail-closed; **no** global fallback.
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

- **Local venv vs CI:** preflight installs `requirements.lock` (includes `fakeredis`). A partial `.venv` misses Redis test deps and fails 6 webhook/throttle tests; run `pip install -r requirements.lock` before `pytest`.
- **Embedded `config/`:** gitignored mirror of `school-analytics-config` for `tests/test_config_schema.py` and optional QA parity; deploy source remains the sibling repo. Copy `config/tools/promote.sh` → `school-analytics-config/scripts/promote.sh` when promoting locally.
- **Analytics consent:** not collected in-app; school-as-service model (see `docs/product/phase2.md`). Migration `014` may leave an unused `parental_consent_records` table until a future cleanup revision.
- **Phase 2 remaining** (see `docs/product/phase2.md`): full bidirectional ERP sync; conflict UI first slice is shipped (`import_conflicts`, admin + API).
- **Test DB pollution:** stale `tests/test_school.db` (+ `.wal`/`.shm`) can cause flaky failures — remove and re-run pytest.
- **`assessment_code` auto-create:** needs UT-style codes (e.g. `UT-MATH-6A-T1`); verify when seeding or importing new assessments.
- **Webhook testing:** each tenant needs `erp.webhookSecretRef` set in config and a matching `SA_SECRET_<REF>` deployment secret before `POST /api/erp/import` will succeed.

## 6. Exact next step

1. **CI (app `main`):** **preflight** green (`5113943f`, run `42564584453`). **supply-chain** green on prior `main` commit `f0c55fd` (`42564283644`); latest tip did not re-run supply-chain. **postgres-integration** failing on `5113943f` — triage separately if blocking.
2. **v1.3.0 image** — `ghcr.io/apoorv-a/school-analytics:1.3.0` published. Optional: `CONFIG_REPO_PAT` + restore `tests/test_config_schema.py` in preflight; until then preflight skips that module.
3. **Config repo — dev/stg:** `environments/{dev,stg}/release.yaml` pin **1.3.0** on `main`; **stg promote 1.3.0** done per operator. Confirm `tenants/sunrise/stg.yaml` `image.tag` is **1.3.0** and `sunrise_stg_v1.3.0` exists before prod.
4. **Config repo — prod prep (done):** `5e6360c` on `main` — `environments/prod/release.yaml` + `tenants/sunrise/prod.yaml` → **1.3.0** only. **Next:** after stg regression green, cut `sunrise_prod_v1.3.0` (do not re-run `promote` — pins already bumped).
5. **Tenant YAML:** `tenants/sunrise/dev.yaml` — `features.bulkImport: true`, `erp.webhookSecretRef`, matching `SA_SECRET_<REF>`; `tenant_operator apply`.
6. **Deferred product:** bidirectional ERP sync and validate-only conflict **preview** before apply (`docs/product/phase2.md`).
7. **Deferred schema:** Alembic cleanup to drop unused `parental_consent_records` from `014`.
8. **Ops runbooks:** [erp-import-conflicts-smoke.md](docs/operations/erp-import-conflicts-smoke.md), [erp-webhook-smoke.md](docs/operations/erp-webhook-smoke.md), [erp-connector-sync.md](docs/operations/erp-connector-sync.md).

## 7. Anything the next agent must not change

- **Do not** reintroduce pill multi-select for subject/class in the filter bar.
- **Do not** add RLS to `tenants` or `tenant_domains`.
- **Do not** add a global ERP webhook secret fallback; keep per-tenant secret refs only.
- **Do not** return created-user passwords in audit/history responses (CLI/upload only).
- **Do not** merge config into the code repo; keep `school-analytics-config` separate.
