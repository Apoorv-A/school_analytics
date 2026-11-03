# ERP webhook smoke test

Use this after a tenant has `features.bulkImport: true` and `erp.webhookSecretRef` (or `erp.webhookSigningSecretRef`) set in [school-analytics-config](https://github.com/Apoorv-A/school-analytics-config), with the matching deployment secret `SA_SECRET_<REF>`.

## Endpoint

`POST /api/erp/import` on the tenant hostname (for example `https://sunrise-dev.school-analytics.test` or local `http://sunrise.localhost:8000`).

Authentication is **HMAC-SHA256** over the raw multipart body:

| Header | Value |
| --- | --- |
| `X-SA-Webhook-Timestamp` | Unix seconds (within 5 minutes of server time) |
| `X-SA-Webhook-Nonce` | Unique string per request (8–128 chars) |
| `X-SA-Webhook-Signature` | `v1=` + hex HMAC of `timestamp + "." + nonce + "." + sha256(body)` |

The signing key is resolved from tenant config secret refs (same as production).

## CLI helper

From the application repo root, with the app running locally:

```bash
export SA_SECRET_SUNRISE_ERP_WEBHOOK='your-signing-key'   # example ref name
PYTHONPATH=. python scripts/erp_webhook_smoke.py \
  --base-url http://sunrise.localhost:8000 \
  --bundle path/to/import.zip \
  --secret-env SA_SECRET_SUNRISE_ERP_WEBHOOK
```

Exit code `0` and JSON on stdout means the webhook accepted the upload (validation/apply details are in the response body).

## Preconditions checklist

1. Tenant operator applied config with `bulkImport: true` and ERP secret refs.
2. Process environment includes the resolved `SA_SECRET_*` value (never commit secrets).
3. Bundle is a ZIP or CSV allowed by the import pipeline (see `docs/onboarding/csv-templates/`).
4. For production-like replay protection, configure Redis replay backend (`ERP_WEBHOOK_REPLAY_BACKEND=redis`); local `DEBUG=true` may use in-memory replay.

## CI reference

Automated coverage lives in `tests/test_erp_webhook.py` (signature, replay, tenant scoping, audit events).
