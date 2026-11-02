"""Deployment secret validation script."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_deploy_secrets import (
    REQUIRED_K8S_SECRET_KEYS,
    erp_secret_env_names,
    keys_from_env,
    keys_from_k8s_secret_manifest,
    main,
    missing_erp_secret_env_vars,
    missing_login_throttle_redis_config,
    missing_replay_redis_config,
    missing_secret_keys,
)


def test_missing_secret_keys_detects_absent_keys():
    assert missing_secret_keys({"database-url"}) == [
        "database-migrate-url",
        "secret-key",
    ]
    assert missing_secret_keys(set(REQUIRED_K8S_SECRET_KEYS)) == []


def test_keys_from_k8s_secret_manifest_reads_data_keys(tmp_path: Path):
    manifest = tmp_path / "secret.yaml"
    manifest.write_text(
        """
apiVersion: v1
kind: Secret
metadata:
  name: school-analytics-secrets
data:
  database-url: abc
  database-migrate-url: def
  secret-key: ghi
""".strip(),
        encoding="utf-8",
    )
    assert keys_from_k8s_secret_manifest(manifest) == {
        "database-url",
        "database-migrate-url",
        "secret-key",
    }


def test_keys_from_env_maps_variables(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://app")
    monkeypatch.setenv("SECRET_KEY", "stable-secret")
    assert keys_from_env() == {"database-url", "secret-key"}


def test_main_exits_zero_when_all_keys_present(capsys):
    keys = ",".join(REQUIRED_K8S_SECRET_KEYS)
    assert main(["--keys", keys, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["missing"] == []


def test_main_exits_one_when_keys_missing(capsys):
    assert main(["--keys", "database-url", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "secret-key" in payload["missing"]


def test_main_check_env_mode_missing(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_MIGRATE_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert main(["--check-env", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_main_check_env_mode_complete(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgres://app")
    monkeypatch.setenv("DATABASE_MIGRATE_URL", "postgres://owner")
    monkeypatch.setenv("SECRET_KEY", "stable-secret")
    assert main(["--check-env", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_erp_secret_env_names_follow_sa_secret_convention():
    assert erp_secret_env_names(["sunrise/erp-webhook"]) == [
        "SA_SECRET_SUNRISE_ERP_WEBHOOK"
    ]


def test_main_erp_secret_refs_requires_matching_env(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgres://app")
    monkeypatch.setenv("DATABASE_MIGRATE_URL", "postgres://owner")
    monkeypatch.setenv("SECRET_KEY", "stable-secret")
    monkeypatch.delenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", raising=False)
    assert main(
        [
            "--check-env",
            "--erp-secret-refs",
            "sunrise/erp-webhook",
            "--json",
        ]
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["missingErpSecretEnv"] == ["SA_SECRET_SUNRISE_ERP_WEBHOOK"]


def test_missing_erp_secret_env_vars_detects_unset(monkeypatch):
    monkeypatch.delenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", raising=False)
    assert missing_erp_secret_env_vars(["sunrise/erp-webhook"]) == [
        "SA_SECRET_SUNRISE_ERP_WEBHOOK"
    ]


def test_missing_replay_redis_config_when_production_backend(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("ERP_WEBHOOK_REPLAY_BACKEND", "redis")
    monkeypatch.delenv("ERP_WEBHOOK_REPLAY_REDIS_URL", raising=False)
    assert missing_replay_redis_config() == ["ERP_WEBHOOK_REPLAY_REDIS_URL"]


def test_missing_replay_redis_config_ignored_in_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("ERP_WEBHOOK_REPLAY_BACKEND", "redis")
    monkeypatch.delenv("ERP_WEBHOOK_REPLAY_REDIS_URL", raising=False)
    assert missing_replay_redis_config() == []


def test_missing_login_throttle_redis_config_when_production_backend(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("LOGIN_THROTTLE_BACKEND", "redis")
    monkeypatch.delenv("LOGIN_THROTTLE_REDIS_URL", raising=False)
    assert missing_login_throttle_redis_config() == ["LOGIN_THROTTLE_REDIS_URL"]


def test_missing_login_throttle_redis_config_ignored_in_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("LOGIN_THROTTLE_BACKEND", "redis")
    monkeypatch.delenv("LOGIN_THROTTLE_REDIS_URL", raising=False)
    assert missing_login_throttle_redis_config() == []


def test_main_check_env_requires_replay_redis_in_production(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgres://app")
    monkeypatch.setenv("DATABASE_MIGRATE_URL", "postgres://owner")
    monkeypatch.setenv("SECRET_KEY", "stable-secret")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("ERP_WEBHOOK_REPLAY_BACKEND", "redis")
    monkeypatch.delenv("ERP_WEBHOOK_REPLAY_REDIS_URL", raising=False)
    assert main(["--check-env", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["missingReplayRedis"] == ["ERP_WEBHOOK_REPLAY_REDIS_URL"]


def test_main_check_env_requires_login_throttle_redis_in_production(
    monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", "postgres://app")
    monkeypatch.setenv("DATABASE_MIGRATE_URL", "postgres://owner")
    monkeypatch.setenv("SECRET_KEY", "stable-secret")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("LOGIN_THROTTLE_BACKEND", "redis")
    monkeypatch.delenv("LOGIN_THROTTLE_REDIS_URL", raising=False)
    assert main(["--check-env", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["missingLoginThrottleRedis"] == ["LOGIN_THROTTLE_REDIS_URL"]
