# Production Support Matrix

Explicit supported versions, operational targets, and escalation paths for School Analytics production deployments (P0 handoff §12.1 item 25).

> **Placeholder contacts:** Replace `TBD` entries before go-live with named on-call rotation and vendor support numbers.

## Supported platform versions

| Component | Supported | Notes |
| --- | --- | --- |
| **Python** | 3.11, 3.12 | CI runs 3.12; Docker image uses `python:3.12-slim`. 3.11 supported per `pyproject.toml`. |
| **PostgreSQL** | 14.x, 15.x, 16.x | Required for production. RLS + owner/runtime role split tested in `postgres-integration` CI. |
| **Kubernetes** | 1.28 – 1.31 | Ingress: nginx-compatible. Helm chart in `deploy/helm/school-analytics/`. |
| **SQLite** | 3.x | **Local dev and tests only** — not supported for production. |

## Application runtime

| Setting | Production requirement |
| --- | --- |
| `DEBUG` | `false` |
| `SECRET_KEY` | Required; stable across restarts (session/MFA/crypto). |
| `DATABASE_URL` | PostgreSQL connection string; runtime role must be **DML-only** (no DDL). |
| Session cookies | `Secure` flag enforced when `DEBUG=false`. |
| `ERP_WEBHOOK_REPLAY_BACKEND` | `redis` in production; `memory` is dev/test only. |
| `ERP_WEBHOOK_REPLAY_REDIS_URL` | Required when replay backend is `redis` and `DEBUG=false`. |
| `LOGIN_THROTTLE_BACKEND` | `redis` in production; `memory` is dev/test only. |
| `LOGIN_THROTTLE_REDIS_URL` | Required when login throttle backend is `redis` and `DEBUG=false`. |
| Migrations | Run via Helm pre-upgrade Job (`alembic upgrade head`) with owner/migrator credentials — not at app startup. |

## MFA requirements

When tenant config sets `auth.mfaEnabled: true` and lists roles in `auth.mfaRequiredRoles`, those roles **must** enrol TOTP before accessing portals/API.

| Role | Typical MFA policy |
| --- | --- |
| `principal`, `admin` | MFA required in production tenants (configure per school). |
| `teacher`, `parent`, `student` | Optional unless explicitly listed in `mfaRequiredRoles`. |

Environment variables: none override tenant MFA policy. MFA secrets are Fernet-encrypted using a key derived from `SECRET_KEY`.

## Backup and recovery targets

See [backup-restore.md](backup-restore.md) for procedures.

| Metric | Production target |
| --- | --- |
| **RPO** (max data loss) | 1 hour (nightly logical backup + WAL when enabled) |
| **RTO** (max restore time) | 4 hours (migrate, restore, validate, cutover) |

Run `scripts/validate_restore.py` after every restore drill; log results in `docs/learnings/`.

## Escalation contacts (placeholder)

| Tier | Contact | When |
| --- | --- | --- |
| L1 — School IT | `TBD — school helpdesk` | User login, browser, hostname/DNS |
| L2 — Platform ops | `TBD — platform on-call` | App 5xx, readiness failures, import errors |
| L3 — Engineering | `TBD — engineering lead` | Data corruption, security incident, failed restore |
| Database vendor | `TBD — managed Postgres provider` | Instance outage, storage, PITR |
| Security incident | `TBD — security contact` | Suspected breach, credential leak, MFA bypass |

## Health and readiness

| Probe | Path | Pass criteria |
| --- | --- | --- |
| Liveness | `/healthz` | HTTP 200 — process alive |
| Readiness | `/readyz` | HTTP 200 — DB reachable and Alembic at head |

## Explicitly NOT supported

- **SQLite in production** — no HA, no RLS parity guarantees at scale.
- **`image.tag: latest`** — Helm chart rejects it; deploy semver tags only.
- **Runtime DDL** — app must not create/alter schema on startup in production.
- **Shared ERP webhook secret across tenants** — each tenant requires `settings_json.erp.webhookSecretRef` plus a matching `SA_SECRET_<REF>` deployment secret.
- **Global per-email login lockout** — throttle keys include `tenant_id`.
- **Unauthenticated ERP webhook ingest** without per-tenant bearer token.
- **Java / legacy `platform/` stack** — retired; Python-only path.
- **Multi-replica login throttle without Redis** — `LOGIN_THROTTLE_BACKEND=memory` is single-replica only; production requires Redis and fails closed when unavailable.
- **Multi-replica ERP webhook replay without Redis** — `ERP_WEBHOOK_REPLAY_BACKEND=memory` is single-replica only; production requires Redis and fails closed when unavailable.
- **SSO / SAML / OIDC** — not in v1 (P2+).
- **Email/SMS delivery for password reset** — admin API returns token once; self-service forgot-password is rate-limited generic response without outbound mail in v1.

## Related runbooks

- [tenant-onboarding.md](tenant-onboarding.md)
- [backup-restore.md](backup-restore.md)
- [supply-chain.md](supply-chain.md)
