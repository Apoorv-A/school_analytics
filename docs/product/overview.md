# Product Overview

## Vision

One platform, four portals, each showing the analytics that stakeholder actually needs, over a full academic year of assessment data. Schools onboard through tenant configuration only — no per-school code changes.

## Personas

| Persona | Portal | Primary need |
| --- | --- | --- |
| Parent / Guardian | Parent | Child's score trend vs class average, improvement and risk signals |
| Student | Student | Own performance, strengths, term progress |
| Teacher | Teacher | Classroom distribution, at-risk students, assessment difficulty |
| Principal / Admin | Admin | School-wide performance, cohort comparison, teacher effectiveness |

## Non-goals (phase 1)

- Separate React SPA (server-rendered Jinja2 is retained)
- Per-tenant Kubernetes releases (one shared app per environment)
- Behavioral telemetry with indefinite raw payload retention

## Success criteria

- Two tenants on one deployment with no cross-tenant data path
- New tenant onboarded by config change + deploy tag only
- Documentation and generated catalogs stay in sync with code (CI verified)
