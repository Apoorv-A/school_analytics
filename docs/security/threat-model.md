# Threat Model (Summary)

## Primary assets

- Student assessment scores, attendance, and teacher remarks (child PII)
- Guardian and staff account credentials

## Key threats and mitigations

| Threat | Mitigation |
| --- | --- |
| Cross-tenant data leak | Hostname binding + session tenant claim + query predicates + RLS on school-owned tables |
| Registry lookup blocked by RLS | `tenants` and `tenant_domains` excluded from RLS; only data tables use `app.tenant_id` |
| Brute-force lockout affecting other tenants | Login throttle keyed by `(tenant_id, email, client IP)` |
| IDOR via student/section IDs | `AccessScope` returns 404 for out-of-scope IDs |
| Parent sees classmate names | `cohort_view()` + `assert_identifiable()` |
| Session hijack | Signed httponly cookies, SameSite=strict, tenant-bound tokens |
| Credential stuffing | Login rate limiting per email+IP hash |
| Secrets in git | Config CI scans tenant YAML; secrets in K8s only |
| SQL injection | SQLAlchemy parameterized queries only |

## Child data

Before pilot with real students: parental consent flow and school DPA required. Per-tenant retention policy supports bounded retention via config.
