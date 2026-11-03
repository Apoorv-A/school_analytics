# School Analytics Platform Documentation

Canonical documentation for the Python multi-tenant school analytics platform.

**For AI agents and new contributors:** start with **[AI_HANDOFF.md](AI_HANDOFF.md)** — repository split, tenancy rules, module map, and invariants.

## Index

| Section | Description |
| --- | --- |
| [AI_HANDOFF.md](AI_HANDOFF.md) | **Start here** — full context for other AIs and engineers |
| [product/overview.md](product/overview.md) | Vision, personas, portals, and product scope |
| [product/erp-strategy.md](product/erp-strategy.md) | ERP coexistence and ingest roadmap |
| [onboarding/school-intake-checklist.md](onboarding/school-intake-checklist.md) | New school onboarding steps |
| [onboarding/csv-templates/](onboarding/csv-templates/) | CSV column headers for ingest |
| [architecture/multi-tenancy.md](architecture/multi-tenancy.md) | Hostname binding, RLS, authorization layers |
| [data/column-dictionary.md](data/column-dictionary.md) | Generated table and column reference |
| [analytics/chart-catalog.yaml](analytics/chart-catalog.yaml) | Machine-readable chart registry |
| [analytics/chart-catalog.md](analytics/chart-catalog.md) | Generated human-readable chart catalog |
| [operations/tenant-onboarding.md](operations/tenant-onboarding.md) | Onboard a school via config only |
| [operations/backup-restore.md](operations/backup-restore.md) | Backup, restore, and RPO/RTO targets |
| [operations/supply-chain.md](operations/supply-chain.md) | Locked dependencies, pip-audit, SBOM |
| [operations/erp-webhook-smoke.md](operations/erp-webhook-smoke.md) | Signed ERP import webhook smoke test |
| [operations/erp-connector-sync.md](operations/erp-connector-sync.md) | Scheduled ERP CSV sync (local drop, SFTP pull) |
| [operations/production-support-matrix.md](operations/production-support-matrix.md) | Supported versions, MFA, escalation, exclusions |
| [onboarding/school-intake-checklist.md](onboarding/school-intake-checklist.md) | CSV templates, validate/apply, bootstrap admin |
| [product/erp-strategy.md](product/erp-strategy.md) | ERP coexistence and ingest roadmap |
| [releases/](releases/) | **Semver release notes** — deployable version history |
| [learnings/](learnings/README.md) | **Failure log** — symptoms, cause, fix (add one file per incident) |
| [security/threat-model.md](security/threat-model.md) | Isolation, secrets, and child-data policy |
| [adr/](adr/) | Architecture decision records |

## Related repository

Tenant YAML, deploy tags, and config CI live in a **separate repo**:

**https://github.com/Apoorv-A/school-analytics-config**

Clone it beside this project when working on onboarding or deployment:

```bash
git clone https://github.com/Apoorv-A/school-analytics-config.git ../school-analytics-config
```

## Regeneration

```bash
python scripts/docs_generate.py
python scripts/docs_verify.py   # fails if generated docs drift
```

## Ownership

| Area | Source of truth |
| --- | --- |
| Data model | `app/models.py` + Alembic migrations |
| Chart catalog | `app/analytics/charts.py` → `docs/analytics/chart-catalog.yaml` |
| Tenant config schema | `tenant_config/models.py` → export via `python -m tenant_operator schema` |
| Tenant YAML files | **school-analytics-config** repo |
| Incident / failure log | `docs/learnings/` in each repo |
| API | FastAPI OpenAPI at `/api/docs` when `DEBUG=true` |
