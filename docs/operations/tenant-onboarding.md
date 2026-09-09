# Tenant Onboarding

## Prerequisites

- PostgreSQL database with migrations applied
- Kubernetes cluster with Helm chart deployed for the target environment
- DNS pointing `tenant.hostname` at the ingress

## Steps

1. Add `config/tenants/<tenant-key>/<env>.yaml` following `tenant.schema.json`.
2. Open PR; `validate-config` workflow must pass (schema, duplicate hostname, no plaintext secrets).
3. Merge and tag: `<tenant>_<env>_v<semver>` (environment release tag for shared deploy).
4. Pipeline runs tenant operator reconcile for changed tenants.
5. Smoke test: `GET https://<hostname>/api/tenant/context` returns expected tenant key.

## Bootstrap admin

Set `bootstrap-admin-email` and `bootstrap-admin-password` in the environment secret on first provision only. Rotate after first login.

## Demo data

`demoData` is allowed in `dev` only. Production schema rejects synthetic demo accounts.
