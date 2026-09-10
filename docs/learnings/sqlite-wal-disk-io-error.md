# 2026-09-10 — SQLite WAL disk I/O error

| Field | Value |
| --- | --- |
| **Repo** | school_analytics |
| **Area** | database / local-dev |
| **Severity** | medium |
| **Status** | resolved |

## Symptoms

- `sqlite3.OperationalError: disk I/O error` on `PRAGMA journal_mode=WAL` during `alembic upgrade head` or app startup (`init_db()`).
- Sidecar files `school.db-wal` and/or `school.db-shm` may exist beside `school.db`.

## Cause

SQLite WAL mode uses `-wal` and `-shm` sidecar files. If the app or a migration is interrupted (crash, kill, forced quit), those files can become stale or corrupt. The next connection that tries to enable or use WAL hits a disk I/O error.

Alembic’s migration engine does **not** set WAL pragmas (it uses a separate engine in `alembic/env.py`). The error usually appears when `app/db.py` connects (app startup or any code path that opens the shared engine) or when stale sidecars break SQLite open even before migrations run.

## Effects

- `alembic upgrade head` fails.
- App startup fails in `init_db()` when auto-migrations run.
- Local dev blocked until sidecars are cleared or WAL is skipped.

## Resolution

1. Stop anything holding the database (uvicorn, pytest with the real `school.db`, etc.).
2. Remove stale sidecars (safe when the app is stopped):

```bash
rm -f school.db-wal school.db-shm
alembic upgrade head
```

3. Code: `app/db.py` catches `OperationalError` on WAL and falls back to `DELETE` journal mode with a warning.

## Prevention

- Stop uvicorn cleanly before deleting the DB or sidecars.
- After an unclean shutdown, delete `school.db-wal` and `school.db-shm` before retrying migrations.
- See [AI_HANDOFF.md](../AI_HANDOFF.md) troubleshooting.

## References

- Fix: `app/db.py` (`_apply_sqlite_pragmas`)
- Default DB path: `sqlite:///./school.db` in `app/config.py`
