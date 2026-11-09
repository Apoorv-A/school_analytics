# Production deploy runbook (v1.3.0)

Production deploys use **school-analytics-config** tag pushes. There is **no** promote workflow for this release — YAML pins are already at `1.3.0`.

## One-time: GitHub `prod` environment

The **deploy** job uses `environment: prod`. Configure in **school-analytics-config** → Settings → Environments → **prod**:

| Item | Action |
| --- | --- |
| **KUBECONFIG** | Add environment secret. Value = **base64** of the production cluster kubeconfig file (`base64 -i kubeconfig` on macOS). The workflow decodes it in `Connect to the cluster`. |
| **Protection rules** | Required reviewers (recommended) so prod deploys need human approval. |
| **Branch / tag policy** | Restrict who can deploy if your org policy requires it. |

If `KUBECONFIG` is missing, deploy fails fast with `No KUBECONFIG secret configured for this environment` (see failed run on `sunrise_prod_v1.3.0` before the secret exists).

Agents and automation **cannot** set this secret — a cluster admin must paste it in the GitHub UI.

## Preconditions

Complete [stg-regression-signoff.md](./stg-regression-signoff.md) first.

| Check | Expected |
| --- | --- |
| `tenants/sunrise/prod.yaml` | `image.tag: 1.3.0` |
| `environments/prod/release.yaml` | `imageTag: "1.3.0"` |
| Platform tag | `v1.3.0` on **school_analytics** (chart checkout in deploy uses this ref) |
| Migrations | Alembic through `016_import_conflicts` on prod DB before or via chart migrate job |

## Cut production

```bash
cd school-analytics-config
git checkout main && git pull
git tag -a sunrise_prod_v1.3.0 -m "Sunrise production v1.3.0"
git push origin sunrise_prod_v1.3.0
```

1. Approve the **deploy** run if environment protection requires it.
2. Verify **Apply** (Helm atomic upgrade) and **Smoke test** succeed.

## Post-deploy smoke (Sunrise prod)

| Check | URL |
| --- | --- |
| Tenant context | `https://sunrise.school-analytics.test/api/tenant/context` |
| Liveness | `https://sunrise.school-analytics.test/healthz` |
| Readiness | `https://sunrise.school-analytics.test/readyz` |

Admin MFA is required for staff roles in prod (`tenants/sunrise/prod.yaml`); use break-glass accounts for smoke if needed.

## Rollback

Push a tag pointing at the **previous** known-good semver (same tenant/env pattern), e.g. `sunrise_prod_v1.2.0`, after restoring matching `image.tag` in `prod.yaml` if pins moved forward.

## Related

- Tenant onboarding: [tenant-onboarding.md](./tenant-onboarding.md)
- Support matrix: [production-support-matrix.md](./production-support-matrix.md)
