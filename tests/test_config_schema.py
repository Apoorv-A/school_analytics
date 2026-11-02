"""Tenant config JSON Schema and embedded config/ tree validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from tenant_config.models import (
    AuthPolicy,
    ErpConfig,
    FeatureFlags,
    TenantConfigDocument,
)

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"
SCHEMA_PATH = CONFIG_ROOT / "schema" / "tenant.schema.json"


@pytest.fixture(scope="module")
def tenant_schema() -> dict:
    assert SCHEMA_PATH.is_file(), f"missing {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_property_keys_match_app_model(tenant_schema: dict) -> None:
    app_props = set(TenantConfigDocument.model_json_schema()["properties"])
    cfg_props = set(tenant_schema["properties"])
    assert app_props == cfg_props

    feature_keys = set(FeatureFlags.model_json_schema()["properties"])
    assert feature_keys == set(tenant_schema["properties"]["features"]["properties"])

    erp_keys = set(ErpConfig.model_json_schema()["properties"])
    cfg_erp_keys = set(tenant_schema["properties"]["erp"]["properties"])
    assert erp_keys == cfg_erp_keys
    assert "webhookToken" not in cfg_erp_keys

    auth_keys = set(AuthPolicy.model_json_schema()["properties"])
    assert auth_keys == set(tenant_schema["properties"]["auth"]["properties"])


def test_embedded_tenant_yamls_validate_with_schema_and_operator(
    tenant_schema: dict,
) -> None:
    Draft202012Validator.check_schema(tenant_schema)
    validator = Draft202012Validator(tenant_schema, format_checker=FormatChecker())
    tenant_dir = CONFIG_ROOT / "tenants"
    paths = sorted(tenant_dir.glob("*/*.yaml"))
    assert paths, "expected tenant YAML under config/tenants"

    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        TenantConfigDocument.model_validate(raw)
        errors = list(validator.iter_errors(raw))
        assert not errors, f"{path}: {errors[0].message}"
