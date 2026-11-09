# Staging regression sign-off (v1.3.0)

Use this checklist before cutting `sunrise_prod_v1.3.0`. Staging must run the same image and config shape as production; promotion is a **new git tag**, not a re-run of `promote.yml`.

## Preconditions (config repo)

| Check | Expected |
| --- | --- |
| App `main` CI | **preflight**, **supply-chain**, and **postgres-integration** green on the commit that built `1.3.0` |
| Image | `ghcr.io/apoorv-a/school-analytics:1.3.0` and git tag `v1.3.0` on **school_analytics** |
| `environments/stg/release.yaml` | `imageTag: "1.3.0"` |
| `tenants/sunrise/stg.yaml` | `image.tag: 1.3.0` (must **match** the deploy tag semver; deploy fails on mismatch) |
| GitHub **stg** environment | Secret `KUBECONFIG` set (base64-encoded kubeconfig) |
| QA secrets (repo) | `QA_*_EMAIL` / `QA_*_PASSWORD` for nightly regression |

If `tenants/sunrise/stg.yaml` still pins an older tag (e.g. `1.0.0`), bump it in a config PR and merge before tagging.

## Deploy to staging

1. Merge config changes on `school-analytics-config` `main`.
2. Create and push the environment tag (annotated tag recommended):

   ```bash
   git checkout main && git pull
   git tag -a sunrise_stg_v1.3.0 -m "Sunrise staging v1.3.0"
   git push origin sunrise_stg_v1.3.0
   ```

3. In GitHub Actions, open the **deploy** workflow run for `sunrise_stg_v1.3.0`.
4. Confirm jobs succeed through **Smoke test** (not only **Connect to the cluster**).

**Do not** use `promote.yml` for this cut — pins are already on `1.3.0`; promotion is tag-only.

## Smoke URLs (Sunrise stg)

| Check | URL / command |
| --- | --- |
| Tenant context | `curl -fsS https://sunrise-stg.school-analytics.test/api/tenant/context` → JSON `"tenant": "sunrise"` |
| Liveness | `GET https://sunrise-stg.school-analytics.test/healthz` → 200 |
| Readiness | `GET https://sunrise-stg.school-analytics.test/readyz` → 200 (DB up) |
| Admin UI | `https://sunrise-stg.school-analytics.test/` (sign-in smoke) |

Deploy workflow runs the tenant-context check automatically; repeat manually if debugging ingress or DNS.

## QA regression

| Method | When |
| --- | --- |
| **Nightly** | **qa-regression** at 01:30 UTC against all `tenants/*/stg.yaml` |
| **On demand** | Actions → **qa-regression** → Run workflow → optional tenant `sunrise` |

Sign-off criteria:

- Latest **deploy** for `sunrise_stg_v1.3.0` green (including smoke).
- At least one **qa-regression** run **after** that deploy with verdict **pass** (workflow_dispatch or nightly).

Download the `qa-report-*` artifact if any role fails; failures also file/comment on a `qa-regression` labeled issue.

## Sign-off record

- [ ] Config pins and tag `sunrise_stg_v1.3.0` pushed
- [ ] Deploy workflow green
- [ ] Manual smoke URLs checked
- [ ] QA regression pass post-deploy
- [ ] Operator notes any open ERP / product gaps (out of scope for this gate — see [prod-deploy-runbook.md](./prod-deploy-runbook.md))

After sign-off, proceed to [prod-deploy-runbook.md](./prod-deploy-runbook.md).
