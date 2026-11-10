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

**Local dev — port 8000 / F01 metrics smoke:** If `uvicorn` logs `Address already in use`, an old process is still bound to `:8000` (often code **without** F01 `/api/metrics` + `/help/metrics`). Free the port, restart from this repo’s venv, then smoke:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN    # note PID
kill <PID>                          # or: kill -9 <PID> if stubborn
source .venv/bin/activate           # use project venv with current tree
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: sunrise.localhost" http://127.0.0.1:8000/api/metrics
# expect 401 (route exists, auth required) — 404 means wrong process or unknown Host / unseeded tenant_domains
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

**Platform build** (uploaded `AI_HANDOFF_CURSOR_SCHOOL_PLATFORM.md`): Wave 1 **F05 teacher student search** is implemented — `GET /api/teacher/students/search`, `/teacher/lookup`, scoped combobox (no email in teacher JSON). Verify: `PYTHONPATH=. pytest -q tests/test_teacher_student_search.py`; teacher login → **Student lookup** → pick a class student.

**F01 complete:** registry (`metric_registry.py`), `tests/test_metric_formulas.py` (12), glossary API + `/help/metrics` (`tests/test_metrics_glossary.py`, 11). Smoke: logged-in `GET /api/metrics`, `GET /help/metrics`. Regenerate: `PYTHONPATH=. python3 app/analytics/metric_registry.py`.

**F01-04 done:** Metrics guide in all portal sidebars (`/help/metrics`); chart cards expose **About this chart** (`GET /api/charts/{key}/info`, `chart_info.js`). Tests: `tests/test_metrics_glossary.py`, `tests/test_chart_metric_info.py`. Verify: log in → sidebar **Metrics guide**; on any chart card click **ℹ** → panel + link to appendix.

**F02 slice done (F02-01 + partial F02-02):** Principal term-trend card copy, term highlight on `school.term_trend`, `chartGuide` on all `school.*` charts, richer **About this chart** panel + mobile sheet CSS. Tests: `tests/test_f02_principal_charts.py`. Verify: Principal **School overview** → term trend subtitle; pick a term → subtitle filter note; **ℹ** on term trend → chart guide + metrics link.

**Next product slice:** **F02 backlog** — chart guides for teacher/section/student charts; optional term-axis visual regression screenshots; **F03** doughnut taxonomy. Optional ledger: `docs/ai_delivery/` (**B00-01**). F05 polish: combobox on teacher overview, `section_id` on search.

**ERP product work is explicitly deferred** (bidirectional sync, conflict preview UI, webhook/connector expansion — see item 5). Prioritize ops gates below.

1. **Staging sign-off:** [stg-regression-signoff.md](docs/operations/stg-regression-signoff.md) — bump `tenants/sunrise/stg.yaml` to `image.tag: 1.3.0` (remote `main` still had `1.0.0` while `environments/stg/release.yaml` is `1.3.0`), ensure **stg** `KUBECONFIG`, push tag `sunrise_stg_v1.3.0`, green **deploy** + **qa-regression**.
2. **Production cut:** [prod-deploy-runbook.md](docs/operations/prod-deploy-runbook.md) — set **prod** environment secret `KUBECONFIG` (manual); then push `sunrise_prod_v1.3.0` (no `promote.yml`). Prior prod tag run failed: missing `KUBECONFIG`.
3. **CI (app `main`):** **postgres-integration** was failing on stale Alembic head assertion (`014` vs `016`); fixed to use `HEAD_REVISION`. Re-run full CI on push.
4. **Deferred product (ERP):** bidirectional ERP sync; validate-only conflict preview before apply — `docs/product/phase2.md`.
5. **Deferred schema:** Alembic cleanup to drop unused `parental_consent_records` from `014`.
6. **v1.3.0 image** — `ghcr.io/apoorv-a/school-analytics:1.3.0` published. Optional: `CONFIG_REPO_PAT` + restore `tests/test_config_schema.py` in preflight.
7. **Tenant dev YAML:** `tenants/sunrise/dev.yaml` — `features.bulkImport: true`, `erp.webhookSecretRef`, matching `SA_SECRET_<REF>`; `tenant_operator apply`.
8. **Ops runbooks (ERP smoke, optional until ERP resumes):** [erp-import-conflicts-smoke.md](docs/operations/erp-import-conflicts-smoke.md), [erp-webhook-smoke.md](docs/operations/erp-webhook-smoke.md), [erp-connector-sync.md](docs/operations/erp-connector-sync.md).

## 7. Anything the next agent must not change

- **Do not** reintroduce pill multi-select for subject/class in the filter bar.
- **Do not** add RLS to `tenants` or `tenant_domains`.
- **Do not** add a global ERP webhook secret fallback; keep per-tenant secret refs only.
- **Do not** return created-user passwords in audit/history responses (CLI/upload only).
- **Do not** merge config into the code repo; keep `school-analytics-config` separate.
