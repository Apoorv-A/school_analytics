---
name: release-and-learnings
description: >-
  Documents semver releases under docs/releases/ and operational incidents under
  docs/learnings/. Use when bumping app version, creating a v* git tag, shipping
  a release, resolving a non-obvious bug/CI failure, or when the user asks to
  record learnings or release notes.
---

# Release and Learnings Workflow

Project-local conventions for version releases and incident documentation in **school_analytics**. Config-repo incidents go in **school-analytics-config** (mirror workflow there).

**Read first:** `docs/AI_HANDOFF.md` for platform context; `docs/product/phase2.md` for product-phase scope (not the same as semver releases).

---

## When to use

| Trigger | Action |
| --- | --- |
| Semver bump in `app/__init__.py` and `pyproject.toml`, or `v*` git tag | Create/update a release doc |
| Non-obvious failure resolved (CI, local dev, tenancy, deploy) | Add a learnings entry |
| User says "document this", "add a learning", "release notes" | Follow the matching workflow below |

---

## Release documentation (semver / tagged release)

### Version sources

- `app/__init__.py` → `__version__`
- `pyproject.toml` → `[project].version`
- CI image publish: `.github/workflows/release.yml` on `v*` tags

Keep both version fields in sync when bumping.

### Create the release doc

1. Add `docs/releases/vX.Y.Z.md` (preferred) or `docs/releases/YYYY-MM-DD-vX.Y.Z.md` when the date helps ordering.
2. Use this structure:

```markdown
# vX.Y.Z — Short title

| Field | Value |
| --- | --- |
| **Date** | YYYY-MM-DD |
| **Tag** | vX.Y.Z |
| **Previous** | vA.B.C |

## Summary

One paragraph: what shipped and why.

## Changes

- User-visible features
- Bug fixes
- Internal/ops improvements

## Migrations

- Alembic revisions (e.g. `004_import_runs`) and whether `alembic upgrade head` is required
- Config or env var changes

## Breaking changes

List API, schema, or deploy changes. Write "None" if none.

## Deploy notes

Image tag, Helm values, config-repo promotion steps if relevant.

## References

- Commits, PRs, related learnings under `docs/learnings/`
```

3. Update `docs/README.md` index with a link to `docs/releases/` if this is the first release doc.
4. Update the "Last updated" line in `docs/AI_HANDOFF.md` when the release is significant.
5. For product-phase scope (themes, delivered slices), update `docs/product/phase2.md` — separate from semver release notes.

### Release checklist

```
- [ ] Bump __version__ in app/__init__.py and pyproject.toml
- [ ] Add docs/releases/vX.Y.Z.md
- [ ] Run migrations locally; note revision IDs in release doc
- [ ] ruff check, pytest, scripts/docs_verify.py
- [ ] Tag vX.Y.Z and push (triggers release workflow)
```

---

## Operational learnings (incidents)

### When to add

Create a learning when debugging took non-obvious effort: CI breaks, local dev blockers, SQLite/Postgres issues, tenancy bugs, import pipeline failures, deploy/operator issues.

Do **not** duplicate trivial fixes (typo, missing import) unless the root cause is surprising.

### Which repo

| Incident area | Repo | Path |
| --- | --- | --- |
| Application code, tests, seed, local dev | school_analytics | `docs/learnings/` |
| Tenant YAML, config CI, promotion, git workflow | school-analytics-config | `docs/learnings/` |

### Steps

1. Copy `docs/learnings/_template.md` to `docs/learnings/YYYY-MM-DD-short-slug.md` (date = day understood; kebab-case slug).
2. Fill **Symptoms**, **Cause**, **Effects**, **Resolution**, **Prevention**, **References**.
3. Add a row to the index table in `docs/learnings/README.md`.
4. If the fix changed invariants or runbooks, update `docs/AI_HANDOFF.md` or the relevant ops doc.

### Existing examples

- `2026-09-10-sqlite-connection-pool-exhaustion.md` — performance / pool sizing
- `sqlite-wal-disk-io-error.md` — stale WAL files (slug may omit date when added mid-release)
- Config repo: push-wrong-repo, validate-config checkout, tenant YAML schema drift

---

## Commit and push

Include docs in the same commit as the code fix or version bump when possible.

```bash
git add docs/releases/ docs/learnings/ app/__init__.py pyproject.toml
git commit -m "Release vX.Y.Z and document <summary>"
git push origin main   # and push tag: git push origin vX.Y.Z
```

For learnings-only commits:

```bash
git add docs/learnings/
git commit -m "Document <short-slug> incident in learnings log"
git push origin main
```

---

## Agent reminders

- Read `docs/learnings/README.md` before debugging recurring issues.
- Product phases (`docs/product/phaseN.md`) track roadmap themes; semver releases (`docs/releases/`) track deployable versions.
- Cross-link learnings and release docs when an incident drove a release fix.
