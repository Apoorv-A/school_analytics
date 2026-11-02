"""Deployment secret resolution."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.secrets import (
    SecretResolutionError,
    normalize_secret_ref,
    resolve_erp_webhook_signing_secret,
    resolve_secret,
    secret_env_name,
    validate_deployed_erp_secrets,
    validate_secret_ref,
)


def test_normalize_secret_ref_maps_to_env_suffix():
    assert normalize_secret_ref("sunrise/erp-webhook") == "SUNRISE_ERP_WEBHOOK"
    assert secret_env_name("sunrise/erp-webhook") == "SA_SECRET_SUNRISE_ERP_WEBHOOK"


def test_validate_secret_ref_rejects_invalid_values():
    with pytest.raises(ValueError, match="secret reference"):
        validate_secret_ref("../etc/passwd")


def test_resolve_secret_from_env(monkeypatch):
    monkeypatch.setenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", "injected-secret")
    assert resolve_secret("sunrise/erp-webhook") == "injected-secret"


def test_resolve_secret_from_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SA_SECRETS_DIR", str(tmp_path))
    secret_file = tmp_path / "sunrise__erp-webhook"
    secret_file.write_text("mounted-secret\n", encoding="utf-8")
    monkeypatch.delenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", raising=False)

    assert resolve_secret("sunrise/erp-webhook") == "mounted-secret"


def test_resolve_secret_required_fails_closed(monkeypatch):
    monkeypatch.delenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", raising=False)
    with pytest.raises(SecretResolutionError):
        resolve_secret("sunrise/erp-webhook", required=True)


def test_signing_secret_ref_preferred_over_webhook_secret_ref(monkeypatch):
    monkeypatch.setenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", "legacy-secret")
    monkeypatch.setenv("SA_SECRET_SUNRISE_ERP_SIGNING", "dedicated-secret")

    assert (
        resolve_erp_webhook_signing_secret(
            webhook_secret_ref="sunrise/erp-webhook",
            webhook_signing_secret_ref="sunrise/erp-signing",
        )
        == "dedicated-secret"
    )


def test_rotation_changes_resolved_secret_without_code_change(monkeypatch):
    monkeypatch.setenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", "version-one")
    assert resolve_secret("sunrise/erp-webhook") == "version-one"

    monkeypatch.setenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", "version-two")
    assert resolve_secret("sunrise/erp-webhook") == "version-two"


def test_resolve_secret_does_not_log_value(caplog, monkeypatch):
    monkeypatch.setenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", "super-secret-value")
    with caplog.at_level(logging.ERROR):
        resolve_secret("sunrise/erp-webhook", required=True)
    assert "super-secret-value" not in caplog.text


def test_validate_deployed_erp_secrets_reports_missing_ref_and_env(monkeypatch):
    monkeypatch.delenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", raising=False)
    tenants = [
        (
            "sunrise",
            {
                "features": {"bulkImport": True},
                "erp": {"webhookSecretRef": "sunrise/erp-webhook"},
            },
        )
    ]
    errors = validate_deployed_erp_secrets(tenants)
    assert any("unresolved ERP webhook secret ref" in message for message in errors)
