"""Deployment secret resolution for tenant configuration references.

Tenant YAML and ``settings_json`` store secret *references* only. Runtime resolves
values from the deployment secret provider:

1. Environment variable ``SA_SECRET_<REF>`` where ``<REF>`` is the reference
   uppercased with non-alphanumeric characters replaced by underscores
   (e.g. ``sunrise/erp-webhook`` → ``SA_SECRET_SUNRISE_ERP_WEBHOOK``).

2. When ``SA_SECRETS_DIR`` is set, a file at ``{SA_SECRETS_DIR}/{safe_name}``
   where ``safe_name`` is the ref with ``/`` replaced by ``__``.

Resolved secret values are never logged.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SECRET_REF_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,126}$", re.IGNORECASE)


class SecretResolutionError(Exception):
    """A required secret reference could not be resolved."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"unable to resolve secret reference: {ref}")


def validate_secret_ref(ref: str) -> str:
    """Return a normalized reference or raise ``ValueError``."""
    cleaned = ref.strip()
    if not cleaned:
        raise ValueError("secret reference must not be empty")
    if not _SECRET_REF_PATTERN.match(cleaned):
        raise ValueError(
            "secret reference must start with a letter or digit and contain only "
            "letters, digits, dots, underscores, slashes, or hyphens"
        )
    return cleaned


def normalize_secret_ref(ref: str) -> str:
    """Map a secret reference to an environment-variable suffix."""
    cleaned = ref.strip()
    if not cleaned:
        return ""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", cleaned.upper())
    return normalized.strip("_")


def secret_env_name(ref: str) -> str:
    return f"SA_SECRET_{normalize_secret_ref(ref)}"


def _secret_file_path(ref: str) -> Path | None:
    secrets_dir = os.environ.get("SA_SECRETS_DIR", "").strip()
    if not secrets_dir:
        return None
    safe_name = ref.strip().replace("/", "__")
    return Path(secrets_dir) / safe_name


def resolve_secret(ref: str, *, required: bool = False) -> str:
    """Resolve a secret reference from the deployment environment."""
    cleaned = ref.strip()
    if not cleaned:
        if required:
            raise SecretResolutionError(ref)
        return ""

    env_name = secret_env_name(cleaned)
    value = os.environ.get(env_name, "").strip()
    if value:
        return value

    file_path = _secret_file_path(cleaned)
    if file_path is not None and file_path.is_file():
        try:
            return file_path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.error(
                "Secret reference %s could not be read from %s",
                cleaned,
                file_path,
            )

    if required:
        logger.error(
            "Secret reference %s could not be resolved (expected %s or mounted file)",
            cleaned,
            env_name,
        )
        raise SecretResolutionError(cleaned)

    return ""


def resolve_erp_webhook_signing_secret(
    *,
    webhook_secret_ref: str,
    webhook_signing_secret_ref: str,
    required: bool = True,
) -> str:
    """Return the ERP webhook signing key, preferring ``webhookSigningSecretRef``."""
    for ref in (webhook_signing_secret_ref.strip(), webhook_secret_ref.strip()):
        if not ref:
            continue
        value = resolve_secret(ref, required=required)
        if value:
            return value
        if required:
            raise SecretResolutionError(ref)
    return ""


def collect_erp_webhook_secret_refs(settings_payload: dict | None) -> list[str]:
    """Return configured ERP webhook secret refs from tenant settings JSON."""
    erp = (settings_payload or {}).get("erp") or {}
    refs: list[str] = []
    signing_ref = str(erp.get("webhookSigningSecretRef") or "").strip()
    webhook_ref = str(erp.get("webhookSecretRef") or "").strip()
    if signing_ref:
        refs.append(signing_ref)
    if webhook_ref and webhook_ref not in refs:
        refs.append(webhook_ref)
    return refs


def validate_deployed_erp_secrets(
    tenants: list[tuple[str, dict | None]],
) -> list[str]:
    """Validate ERP webhook secret refs for tenants with bulk import enabled."""
    errors: list[str] = []
    for tenant_key, settings_payload in tenants:
        features = (settings_payload or {}).get("features") or {}
        if not bool(features.get("bulkImport", False)):
            continue

        erp = (settings_payload or {}).get("erp") or {}
        webhook_ref = str(erp.get("webhookSecretRef") or "").strip()
        signing_ref = str(erp.get("webhookSigningSecretRef") or "").strip()
        if not webhook_ref and not signing_ref:
            errors.append(
                f"tenant {tenant_key}: bulkImport enabled but no ERP webhook secret ref"
            )
            continue

        try:
            resolve_erp_webhook_signing_secret(
                webhook_secret_ref=webhook_ref,
                webhook_signing_secret_ref=signing_ref,
                required=True,
            )
        except SecretResolutionError as exc:
            errors.append(
                f"tenant {tenant_key}: unresolved ERP webhook secret ref {exc.ref}"
            )
    return errors
