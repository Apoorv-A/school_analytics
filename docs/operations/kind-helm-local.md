# kind + Helm (local Mac arm64)

Run the FastAPI app on a local [kind](https://kind.sigs.k8s.io/) cluster with the chart in `deploy/helm/school-analytics`.

## Prerequisites

```bash
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
kind get clusters   # expect: school-analytics
kubectl config use-context kind-school-analytics
```

## What the chart expects

| Item | Notes |
|------|--------|
| Secret `school-analytics-secrets` | Keys: `database-url`, `database-migrate-url`, `secret-key`, `erp-webhook-replay-redis-url`, `login-throttle-redis-url` |
| Migrations | Pre-install hook Job runs `alembic upgrade head` using `database-migrate-url` |
| Redis | Required when `DEBUG=false` (chart default): login throttle and ERP replay use Redis URLs from the secret |
| Ingress | Optional; values list `ingress.hosts` but no Ingress template is shipped yet — use `kubectl port-forward` |
| Operator | `operator.enabled` in values only; no Operator Deployment template in this chart |

For a single-replica dev cluster, use one Postgres user for both `database-url` and `database-migrate-url` (same as `deploy/docker-compose.yml`).

## 1. Postgres + Redis (in-cluster)

```bash
kubectl apply -f deploy/kind/postgres-redis.yaml
kubectl rollout status deployment/postgres --timeout=120s
kubectl rollout status deployment/redis --timeout=120s
```

## 2. App secret

```bash
kubectl create secret generic school-analytics-secrets \
  --from-literal=database-url='postgresql+psycopg://school:school@postgres:5432/school_analytics' \
  --from-literal=database-migrate-url='postgresql+psycopg://school:school@postgres:5432/school_analytics' \
  --from-literal=secret-key='local-kind-dev-secret-change-me' \
  --from-literal=erp-webhook-replay-redis-url='redis://redis:6379/0' \
  --from-literal=login-throttle-redis-url='redis://redis:6379/1' \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 3. Build and load the **Python** image

The chart image must be the repo `deploy/Dockerfile` app (uvicorn on port 8000, `alembic` on PATH). Do **not** reuse an unrelated local tag named `school-analytics:local` (for example a Java/Spring image).

If `docker build -f deploy/Dockerfile` fails on PyPI TLS inside Docker:

```bash
cd /path/to/school_analytics
source .venv/bin/activate
bash deploy/scripts/fetch_linux_arm64_wheels.sh
docker build -f deploy/Dockerfile.kind -t school-analytics:local .
kind load docker-image school-analytics:local --name school-analytics
```

`Dockerfile.kind` installs from `deploy/docker-wheels-linux` and adds `psycopg[binary]` (not in `requirements.lock` but required for `postgresql+psycopg://` URLs).

## 4. Helm install

```bash
helm upgrade --install school-analytics deploy/helm/school-analytics \
  --set image.repository=school-analytics \
  --set-string image.tag=local \
  --set image.pullPolicy=Never \
  --set ingress.enabled=false \
  --set migrations.enabled=true \
  --wait --timeout 8m
```

Optional overrides:

- `--set loginThrottle.backend=memory` is **not** valid with `DEBUG=false` (app refuses to start without Redis-backed throttle).
- `operator.enabled` has no effect until an operator template exists.

## 5. Access the API

```bash
kubectl get pods -l app=school-analytics
kubectl port-forward svc/school-analytics 8000:80
curl -sS http://127.0.0.1:8000/healthz
# {"status":"ok","version":"..."}
```

Tenant hostnames (for example `sunrise.localhost`) are resolved by app middleware; with port-forward, use the `Host` header or add an Ingress controller later.

## After `/healthz`

Once `curl -sS http://127.0.0.1:8000/healthz` returns `{"status":"ok","version":"..."}`, finish database readiness, register a tenant, and open the Sunrise principal portal.

### Readiness

```bash
curl -sS http://127.0.0.1:8000/readyz
# {"status":"ok","database":"ok","migrations":"ok"}
```

`503` here means Postgres is unreachable or Alembic is not at head — check the migration hook Job logs before provisioning tenants.

### Reach Sunrise on `sunrise.localhost`

The chart does not ship an Ingress template yet (`ingress.enabled=false` for kind). Keep a port-forward to the Service and use a tenant hostname the registry knows:

```bash
kubectl port-forward svc/school-analytics 8000:80
```

On macOS, `sunrise.localhost` usually resolves to `127.0.0.1` (RFC 6761). Then open **http://sunrise.localhost:8000** in a browser — the `Host` header is `sunrise.localhost`, which tenant middleware needs.

Without `*.localhost` DNS, curl still works:

```bash
curl -sS -H 'Host: sunrise.localhost' http://127.0.0.1:8000/api/tenant/context
```

Optional Ingress later: install an ingress controller on kind, set `ingress.enabled=true`, and add `sunrise.localhost` to chart values — not required for local smoke tests.

### Provision the Sunrise tenant

Two supported paths:

**A — Config operator (registry + school shell only)**

Copies tenant policy from YAML into Postgres. Does not create students, scores, or the seeded principal unless you pass bootstrap env vars.

```bash
POD=$(kubectl get pod -l app=school-analytics -o jsonpath='{.items[0].metadata.name}')
kubectl cp deploy/kind/tenants/sunrise-localhost.yaml "${POD}:/tmp/sunrise.yaml"
kubectl exec deploy/school-analytics -- python -m tenant_operator apply --config=/tmp/sunrise.yaml
```

First principal login (only when no admin exists yet):

```bash
kubectl exec deploy/school-analytics -- env \
  BOOTSTRAP_ADMIN_EMAIL=principal@sunrise.edu \
  BOOTSTRAP_ADMIN_PASSWORD='Choose-A-Strong-Password' \
  python -m tenant_operator apply --config=/tmp/sunrise.yaml
```

Equivalent from the app repo with Postgres forwarded:

```bash
kubectl port-forward svc/postgres 5433:5432 &
export DATABASE_URL='postgresql+psycopg://school:school@127.0.0.1:5433/school_analytics'
PYTHONPATH=. python -m tenant_operator apply \
  --config=deploy/kind/tenants/sunrise-localhost.yaml
```

Or use the external config repo file with hostname overridden — production dev YAML uses `sunrise-dev.school-analytics.test`, not `sunrise.localhost`:

```bash
PYTHONPATH=. python -m tenant_operator apply \
  --config=../school-analytics-config/tenants/sunrise/dev.yaml
# only after changing hostname to sunrise.localhost or using Host header that matches YAML
```

**B — Demo seed (recommended for dashboards and `/admin/import`)**

Loads Sunrise + Horizon synthetic data, `bulkImport: true`, and demo accounts. Safe on an empty database; do **not** use `--reset` against Postgres in kind (it drops tables without re-running Alembic).

```bash
kubectl exec deploy/school-analytics -- python -m seed.generate
```

### Sign in as principal and open Data import

1. `kubectl port-forward svc/school-analytics 8000:80` (if not already running).
2. Open **http://sunrise.localhost:8000/login**.
3. Sign in:
   - **Email:** `principal@sunrise.edu`
   - **Password:** `Demo@12345` (from `seed.generate`; not printed on the login page unless `DEMO_MODE=true`)
4. After redirect to the admin home, open **http://sunrise.localhost:8000/admin/import** (or **Data import** in the admin nav).

Quick API check:

```bash
curl -sS -H 'Host: sunrise.localhost' http://127.0.0.1:8000/api/tenant/context
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Migrate job: `exec: "alembic": executable file not found` | Image is not the Python app — rebuild with `deploy/Dockerfile` or `deploy/Dockerfile.kind` |
| `CreateContainerConfigError` / non-numeric user `app` | Rebuild with `deploy/Dockerfile.kind` (uses `USER 1000:1000`) |
| App crash: login throttle Redis | Ensure Redis is running and secret keys are set |
| `image.tag must be set` | Pass `--set-string image.tag=local` (chart rejects empty/`latest`) |

## Teardown

```bash
helm uninstall school-analytics
kubectl delete -f deploy/kind/postgres-redis.yaml
kubectl delete secret school-analytics-secrets
```
