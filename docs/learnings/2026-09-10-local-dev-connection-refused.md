# 2026-09-10 — Local dev connection refused

| Field | Value |
| --- | --- |
| **Repo** | school_analytics |
| **Area** | local-dev |
| **Severity** | low |
| **Status** | resolved |

## Symptoms

- Browser: `ERR_CONNECTION_REFUSED` at `http://sunrise.localhost:8000` and `http://horizon.localhost:8000`.
- Error code -102; nothing listening on port 8000.

## Cause

Uvicorn was not running. Optional: missing `.env`, database not seeded — but connection refused specifically means no server process on the port.

## Effects

- Cannot demo or test the app locally until server is started.

## Resolution

```bash
cd school_analytics
source .venv/bin/activate
cp .env.example .env   # SECRET_KEY, DEMO_MODE=true, DEMO_PASSWORD=Demo@12345
python -m seed.generate --reset
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On macOS, `*.localhost` usually resolves without `/etc/hosts`. `Demo@12345` is the **app** password, not the Mac `sudo` password.

## Prevention

- Documented full local flow in `docs/AI_HANDOFF.md` and root `README.md`.
- Check `lsof -i :8000` if unsure whether uvicorn is up.

## References

- Demo hostnames: `sunrise.localhost`, `horizon.localhost` (see `seed/generate.py`)
