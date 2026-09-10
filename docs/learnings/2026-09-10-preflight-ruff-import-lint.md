# 2026-09-10 — Preflight CI failed on Ruff lint

| Field | Value |
| --- | --- |
| **Repo** | school_analytics |
| **Area** | ci |
| **Severity** | medium |
| **Status** | resolved |

## Symptoms

- GitHub Actions `preflight` workflow failed at the **Ruff** step (runs 34417415928, 34420105959, 34420237329).
- Exit code 1; later steps (pytest, docs, helm) did not run.
- Errors included `I001` (import sort), `F401` (unused imports), `UP042` (`StrEnum`), `E501` (line length), `S603`/`S607`/`RUF100` (subprocess in `scripts/docs_verify.py`).

## Cause

Multi-tenant work landed without running the same Ruff command CI uses:

```bash
ruff check app seed tests tenant_config tenant_operator scripts
```

Notable traps:

- `app/models.py`: isort places `JSON` **before** `Boolean` in the `sqlalchemy` import tuple.
- `scripts/docs_verify.py`: bare `"git"` triggers S607; stale `# noqa` triggers RUF100.

## Effects

- `main` branch CI red after push.
- Several fix-and-push cycles required.

## Resolution

- Ran `ruff check --fix` on affected paths.
- `tenant_config/models.py`: `TenantEnvironment` → `StrEnum`.
- `scripts/docs_verify.py`: resolve git via `shutil.which("git")`.
- `app/models.py`: final import order with `JSON` first in sqlalchemy block.
- Successful run: 34420373544 (commit `04c503b`).

## Prevention

- Before push: `ruff check app seed tests tenant_config tenant_operator scripts`
- Optionally add a pre-commit hook or document in AI handoff.
- After large imports refactors, run Ruff locally — do not rely on pytest alone.

## References

- Workflow: `.github/workflows/preflight.yml`
- Config: `pyproject.toml` `[tool.ruff]`
