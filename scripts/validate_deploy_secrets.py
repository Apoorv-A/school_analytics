"""Validate Kubernetes deployment secret keys for School Analytics.

Helm charts expect a secret (default ``school-analytics-secrets``) with:

- ``database-url`` — runtime app ``DATABASE_URL`` (DML / RLS role)
- ``database-migrate-url`` — migration job ``DATABASE_URL`` (owner / DDL role)
- ``secret-key`` — application ``SECRET_KEY`` for sessions and signing

ERP webhook secrets are referenced from tenant config (``erp.webhookSecretRef``) and
resolved at runtime from ``SA_SECRET_<REF>`` environment variables. Use
``--erp-secret-refs`` to ensure the matching env vars or K8s secret keys exist.

Usage::

    python scripts/validate_deploy_secrets.py --keys database-url,secret-key,database-migrate-url
    python scripts/validate_deploy_secrets.py --secret-file deploy/k8s/secret.yaml
    python scripts/validate_deploy_secrets.py --check-env
    python scripts/validate_deploy_secrets.py --check-env --erp-secret-refs sunrise/erp-webhook
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.secrets import secret_env_name  # noqa: E402

REQUIRED_K8S_SECRET_KEYS: tuple[str, ...] = (
    "database-url",
    "database-migrate-url",
    "secret-key",
)

ENV_SECRET_MAP: dict[str, str] = {
    "database-url": "DATABASE_URL",
    "database-migrate-url": "DATABASE_MIGRATE_URL",
    "secret-key": "SECRET_KEY",
}

OPTIONAL_ENV_SECRET_MAP: dict[str, str] = {
    "erp-webhook-replay-redis-url": "ERP_WEBHOOK_REPLAY_REDIS_URL",
    "login-throttle-redis-url": "LOGIN_THROTTLE_REDIS_URL",
}


def missing_secret_keys(present_keys: set[str]) -> list[str]:
    """Return required Helm secret keys that are absent from ``present_keys``."""
    return [key for key in REQUIRED_K8S_SECRET_KEYS if key not in present_keys]


def keys_from_k8s_secret_manifest(path: Path) -> set[str]:
    """Read ``data`` keys from a Kubernetes Secret manifest."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"invalid manifest: {path}")
    data = document.get("data") or document.get("stringData") or {}
    if not isinstance(data, dict):
        raise ValueError(f"secret manifest has no data keys: {path}")
    return {str(key) for key in data}


def keys_from_env() -> set[str]:
    """Map configured environment variables back to expected secret key names."""
    present: set[str] = set()
    for secret_key, env_name in ENV_SECRET_MAP.items():
        if os.environ.get(env_name, "").strip():
            present.add(secret_key)
    return present


def erp_secret_env_names(refs: list[str]) -> list[str]:
    """Return ``SA_SECRET_<REF>`` env var names for ERP webhook secret refs."""
    names: list[str] = []
    for ref in refs:
        cleaned = ref.strip()
        if cleaned:
            names.append(secret_env_name(cleaned))
    return names


def missing_erp_secret_env_vars(refs: list[str]) -> list[str]:
    """Return ERP secret env vars that are unset or empty."""
    return [
        env_name
        for env_name in erp_secret_env_names(refs)
        if not os.environ.get(env_name, "").strip()
    ]


def missing_replay_redis_config() -> list[str]:
    """Return replay Redis config problems when production-style env is detected."""
    backend = os.environ.get("ERP_WEBHOOK_REPLAY_BACKEND", "memory").strip().lower()
    debug = os.environ.get("DEBUG", "false").strip().lower() in {"1", "true", "yes"}
    if debug or backend != "redis":
        return []
    redis_url = os.environ.get("ERP_WEBHOOK_REPLAY_REDIS_URL", "").strip()
    if not redis_url:
        return ["ERP_WEBHOOK_REPLAY_REDIS_URL"]
    return []


def missing_login_throttle_redis_config() -> list[str]:
    """Return login throttle Redis config problems in production-style env."""
    backend = os.environ.get("LOGIN_THROTTLE_BACKEND", "memory").strip().lower()
    debug = os.environ.get("DEBUG", "false").strip().lower() in {"1", "true", "yes"}
    if debug or backend != "redis":
        return []
    redis_url = os.environ.get("LOGIN_THROTTLE_REDIS_URL", "").strip()
    if not redis_url:
        return ["LOGIN_THROTTLE_REDIS_URL"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keys",
        help="Comma-separated secret keys present (for CI or manual checks)",
    )
    parser.add_argument(
        "--secret-file",
        type=Path,
        help="Kubernetes Secret manifest YAML to inspect",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="Validate DATABASE_URL, DATABASE_MIGRATE_URL, and SECRET_KEY env vars",
    )
    parser.add_argument(
        "--erp-secret-refs",
        help="Comma-separated ERP webhook secret refs to validate as SA_SECRET_<REF> env vars",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON on stdout",
    )
    args = parser.parse_args(argv)

    present: set[str] = set()
    if args.keys:
        present |= {item.strip() for item in args.keys.split(",") if item.strip()}
    if args.secret_file:
        present |= keys_from_k8s_secret_manifest(args.secret_file)
    if args.check_env:
        present |= keys_from_env()

    if not present and not args.check_env and not args.keys and not args.secret_file:
        parser.error("provide --keys, --secret-file, or --check-env")

    missing = missing_secret_keys(present)
    erp_refs = [
        item.strip()
        for item in (args.erp_secret_refs or "").split(",")
        if item.strip()
    ]
    missing_erp_env = missing_erp_secret_env_vars(erp_refs) if erp_refs else []
    missing_replay_redis = missing_replay_redis_config() if args.check_env else []
    missing_throttle_redis = (
        missing_login_throttle_redis_config() if args.check_env else []
    )
    report = {
        "required": list(REQUIRED_K8S_SECRET_KEYS),
        "present": sorted(present),
        "missing": missing,
        "erpSecretRefs": erp_refs,
        "missingErpSecretEnv": missing_erp_env,
        "missingReplayRedis": missing_replay_redis,
        "missingLoginThrottleRedis": missing_throttle_redis,
        "ok": (
            not missing
            and not missing_erp_env
            and not missing_replay_redis
            and not missing_throttle_redis
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Required secret keys:", ", ".join(REQUIRED_K8S_SECRET_KEYS))
        print("Present:", ", ".join(sorted(present)) or "(none)")
        if missing:
            print("Missing:", ", ".join(missing))
        else:
            print("All required deployment secret keys are present.")
        if erp_refs:
            if missing_erp_env:
                print("Missing ERP secret env vars:", ", ".join(missing_erp_env))
            else:
                print("All ERP webhook secret env vars are present.")
        if missing_replay_redis:
            print(
                "Missing replay Redis config:",
                ", ".join(missing_replay_redis),
            )
        if missing_throttle_redis:
            print(
                "Missing login throttle Redis config:",
                ", ".join(missing_throttle_redis),
            )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
