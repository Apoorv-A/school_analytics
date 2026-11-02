"""Cross-repo contract expectations shared with school-analytics-config QA.

The config repo's ``qa/regression.py`` drives nightly checks against a live deployment.
These tests encode the same status-code contracts so drift is caught in ``pytest`` before
deploy.

When ``../school-analytics-config`` is cloned beside this repo, additional parity tests
validate tenant YAML and shared constants against the live config tree.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from app.models import Role, User
from seed.generate import DEMO_PASSWORD
from tenant_config.models import (
    AuthPolicy,
    ErpConfig,
    FeatureFlags,
    TenantConfigDocument,
)
from tenant_config.version_rules import RELEASED as APP_RELEASED

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_REPO_CANDIDATES = (
    PROJECT_ROOT.parent / "school-analytics-config",
    PROJECT_ROOT / "school-analytics-config",
    PROJECT_ROOT / "config",
)


def _tenant_schema_path(config_repo: Path) -> Path:
    for candidate in (
        config_repo / "tenant.schema.json",
        config_repo / "schema" / "tenant.schema.json",
    ):
        if candidate.is_file():
            return candidate
    raise AssertionError(f"tenant schema not found under {config_repo}")


def _config_repo_root() -> Path | None:
    for candidate in CONFIG_REPO_CANDIDATES:
        if (candidate / "qa" / "regression.py").is_file():
            return candidate
    return None

# Mirrors qa/regression.py ROLE_FORBIDDEN_CHART — one chart each role must not reach.
ROLE_FORBIDDEN_CHART = {
    "teacher": "school.kpis",
    "parent": "section.kpis",
    "student": "school.at_risk",
}


class TestChartAuthContract:
    @pytest.mark.parametrize(
        ("fixture_name", "key"),
        [
            ("teacher_context", ROLE_FORBIDDEN_CHART["teacher"]),
            ("parent_context", ROLE_FORBIDDEN_CHART["parent"]),
            ("student_context", ROLE_FORBIDDEN_CHART["student"]),
        ],
    )
    def test_forbidden_chart_returns_404_not_403(self, fixture_name, key, request):
        ctx = request.getfixturevalue(fixture_name)
        response = ctx["client"].get(f"/api/charts/{key}")
        assert response.status_code == 404

    def test_unknown_chart_key_is_404(self, admin_client):
        assert admin_client.get("/api/charts/school.not_a_chart").status_code == 404

    def test_anonymous_chart_request_is_401(self, anon_client):
        assert anon_client.get("/api/charts/school.kpis").status_code == 401


class TestApiLoginContract:
    def test_json_login_sets_session_and_returns_profile(self, anon_client, db):
        email = db.scalars(
            select(User.email).where(User.role == Role.ADMIN).limit(1)
        ).one()
        response = anon_client.post(
            "/api/auth/login",
            json={"email": email, "password": DEMO_PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == Role.ADMIN.value
        assert body["mfaPending"] is False
        assert body["mfaEnrolmentNeeded"] is False
        assert isinstance(body["userId"], int)
        assert anon_client.get("/api/charts/school.kpis").status_code == 200

    def test_json_login_bad_credentials_return_401(self, anon_client):
        response = anon_client.post(
            "/api/auth/login",
            json={"email": "no.such.person@sunrise.edu", "password": "wrong"},
        )
        assert response.status_code == 401
        assert "error" in response.json()

    def test_form_login_still_works(self, anon_client, db):
        email = db.scalars(
            select(User.email).where(User.role == Role.ADMIN).limit(1)
        ).one()
        response = anon_client.post(
            "/login", data={"email": email, "password": DEMO_PASSWORD}
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin"


class TestHealthContract:
    def test_healthz_is_liveness_only(self, anon_client):
        response = anon_client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "database" not in body

    def test_readyz_reports_database(self, anon_client):
        response = anon_client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["migrations"] == "ok"

    def test_readyz_returns_503_when_migrations_pending(self, anon_client, monkeypatch):
        monkeypatch.setattr(
            "app.db.check_db_migrations_at_head",
            lambda conn_engine=None: False,
        )
        response = anon_client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["database"] == "ok"
        assert body["migrations"] == "pending"


def _load_role_forbidden_from_regression(path: Path) -> dict[str, str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ROLE_FORBIDDEN_CHART":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, dict):
                        return value
    raise AssertionError(f"ROLE_FORBIDDEN_CHART not found in {path}")


class TestConfigRepoParity:
    """Optional checks when school-analytics-config is checked out locally."""

    @pytest.fixture(scope="class")
    def config_repo(self) -> Path | None:
        return _config_repo_root()

    def test_role_forbidden_chart_matches_regression_runner(self, config_repo):
        if config_repo is None:
            pytest.skip("school-analytics-config not checked out beside app repo")
        from_config = _load_role_forbidden_from_regression(
            config_repo / "qa" / "regression.py"
        )
        assert from_config == ROLE_FORBIDDEN_CHART

    def test_tenant_schema_property_keys_match_app_model(self, config_repo):
        if config_repo is None:
            pytest.skip("school-analytics-config not checked out beside app repo")
        schema = json.loads(_tenant_schema_path(config_repo).read_text(encoding="utf-8"))
        app_props = set(TenantConfigDocument.model_json_schema()["properties"])
        cfg_props = set(schema["properties"])
        assert app_props == cfg_props

        feature_keys = set(FeatureFlags.model_json_schema()["properties"])
        cfg_feature_keys = set(schema["properties"]["features"]["properties"])
        assert feature_keys == cfg_feature_keys

        erp_keys = set(ErpConfig.model_json_schema()["properties"])
        cfg_erp_keys = set(schema["properties"]["erp"]["properties"])
        assert erp_keys == cfg_erp_keys
        assert "webhookToken" not in cfg_erp_keys

        auth_keys = set(AuthPolicy.model_json_schema()["properties"])
        cfg_auth_keys = set(schema["properties"]["auth"]["properties"])
        assert auth_keys == cfg_auth_keys

    def test_config_tenant_yamls_validate_with_app_model(self, config_repo):
        if config_repo is None:
            pytest.skip("school-analytics-config not checked out beside app repo")
        for path in sorted((config_repo / "tenants").glob("*/*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            TenantConfigDocument.model_validate(raw)
            assert "webhookToken" not in (raw.get("erp") or {})

    def test_version_rules_match_config_repo(self, config_repo):
        if config_repo is None:
            pytest.skip("school-analytics-config not checked out beside app repo")
        text = (config_repo / "scripts" / "version_rules.py").read_text(encoding="utf-8")
        match = re.search(
            r'RELEASED = re\.compile\(r(["\'])(.+?)\1\)',
            text,
        )
        assert match is not None, "RELEASED pattern not found in config version_rules.py"
        assert APP_RELEASED.pattern == match.group(2)

    def test_no_stale_mfa_comments_in_tenant_yamls(self, config_repo):
        if config_repo is None:
            pytest.skip("school-analytics-config not checked out beside app repo")
        stale = re.compile(r"MFA is not implemented", re.IGNORECASE)
        for path in (config_repo / "tenants").rglob("*.yaml"):
            for line in path.read_text(encoding="utf-8").splitlines():
                assert not stale.search(line), f"stale MFA comment in {path}"
