# Operational Learnings

Capture failures, root causes, user-visible effects, and fixes here. One file per incident.

**For AI agents:** read this index before debugging recurring issues. Full platform context remains in [../AI_HANDOFF.md](../AI_HANDOFF.md).

## When to add an entry

Create a new file when you hit a failure that took non-obvious debugging — CI breaks, local dev blockers, tenancy bugs, deploy/operator issues, etc.

## How to add an entry

1. Copy [_template.md](_template.md) to `YYYY-MM-DD-short-slug.md` (date = day the issue was understood, kebab-case slug).
2. Fill in **Symptoms**, **Cause**, **Effects**, **Resolution**, and **Prevention**.
3. Link the file in the table below.
4. If the fix changed code or runbooks, update [AI_HANDOFF.md](../AI_HANDOFF.md) or the relevant ops doc.

## Index

| Date | Slug | Summary |
| --- | --- | --- |
| 2026-09-10 | [sqlite-connection-pool-exhaustion](2026-09-10-sqlite-connection-pool-exhaustion.md) | Dashboard stalled 20–30s; SQLite pool size 5 exhausted by parallel chart API calls |
| 2026-09-10 | [preflight-ruff-import-lint](2026-09-10-preflight-ruff-import-lint.md) | CI preflight failed on Ruff I001/F401/S607 across imports and scripts |
| 2026-09-10 | [local-dev-connection-refused](2026-09-10-local-dev-connection-refused.md) | Browser `ERR_CONNECTION_REFUSED`; uvicorn not running / no seed |

## Related

Config-repo learnings live in **school-analytics-config** under `docs/learnings/` (push wrong repo, nested checkout deleted, etc.).
