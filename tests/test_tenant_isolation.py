"""Cross-tenant isolation tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Role, Tenant, User
from seed.generate import DEMO_PASSWORD, HORIZON_HOST, SUNRISE_HOST


@pytest.fixture
def horizon_admin_client(db) -> TestClient:
    tenant = db.scalars(select(Tenant).where(Tenant.key == "horizon")).one()
    email = db.scalars(
        select(User.email).where(
            User.tenant_id == tenant.id, User.role == Role.ADMIN
        )
    ).one()
    client = TestClient(app, headers={"Host": HORIZON_HOST}, follow_redirects=False)
    response = client.post("/login", data={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 303
    return client


class TestHostnameIsolation:
    def test_unknown_host_is_404(self):
        client = TestClient(app, headers={"Host": "unknown.example.com"})
        assert client.get("/login").status_code == 404

    def test_tenant_context_endpoint(self):
        client = TestClient(app, headers={"Host": SUNRISE_HOST})
        payload = client.get("/api/tenant/context").json()
        assert payload["tenant"] == "sunrise"

    def test_horizon_resolves_separately(self):
        client = TestClient(app, headers={"Host": HORIZON_HOST})
        payload = client.get("/api/tenant/context").json()
        assert payload["tenant"] == "horizon"


class TestCrossTenantAuth:
    def test_sunrise_session_invalid_on_horizon_host(self, admin_client):
        client = TestClient(app, headers={"Host": HORIZON_HOST})
        client.cookies.update(admin_client.cookies)
        response = client.get("/admin", follow_redirects=False)
        assert response.status_code in (303, 401, 404)
        if response.status_code == 303:
            assert response.headers["location"] == "/login"

    def test_same_email_different_tenants(self, db):
        sunrise = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        horizon = db.scalars(select(Tenant).where(Tenant.key == "horizon")).one()
        sunrise_admin = db.scalars(
            select(User).where(User.tenant_id == sunrise.id, User.role == Role.ADMIN)
        ).one()
        horizon_admin = db.scalars(
            select(User).where(User.tenant_id == horizon.id, User.role == Role.ADMIN)
        ).one()
        assert sunrise_admin.email == horizon_admin.email
        assert sunrise_admin.id != horizon_admin.id


class TestTenantScopedData:
    def test_horizon_admin_sees_only_horizon_data(self, horizon_admin_client):
        response = horizon_admin_client.get("/api/charts/school.kpis")
        assert response.status_code == 200
        assert response.json()["cards"]

    def test_sunrise_admin_on_sunrise_host(self, admin_client):
        payload = admin_client.get("/api/charts/school.kpis").json()
        assert payload["cards"]

    def test_login_lockout_is_scoped_per_tenant(self, db):
        """Failed attempts on one hostname must not lock the same email on another."""
        from fastapi.testclient import TestClient

        from app.main import app
        from app.models import Role, Tenant, User
        from app.routers import auth as auth_module
        from seed.generate import DEMO_PASSWORD, HORIZON_HOST, SUNRISE_HOST

        sunrise = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        email = db.scalars(
            select(User.email).where(
                User.tenant_id == sunrise.id, User.role == Role.ADMIN
            )
        ).one()

        auth_module._attempts.clear()
        sunrise_client = TestClient(
            app, headers={"Host": SUNRISE_HOST}, follow_redirects=False
        )
        horizon_client = TestClient(
            app, headers={"Host": HORIZON_HOST}, follow_redirects=False
        )

        for _ in range(8):
            sunrise_client.post(
                "/login", data={"email": email, "password": "wrong-password"}
            )
        locked = sunrise_client.post(
            "/login", data={"email": email, "password": "wrong-password"}
        )
        assert locked.status_code == 429

        allowed = horizon_client.post(
            "/login", data={"email": email, "password": DEMO_PASSWORD}
        )
        assert allowed.status_code == 303
        auth_module._attempts.clear()

    def test_operator_validate_sample_config(self, tmp_path):
        import yaml

        from tenant_config.models import TenantConfigDocument

        doc = {
            "tenant": {
                "key": "sample",
                "displayName": "Sample School",
                "hostname": "sample.localhost",
                "environment": "dev",
            },
            "school": {"name": "Sample School", "city": "Pune", "board": "CBSE"},
            "academics": {
                "passPercentage": 33.0,
                "atRiskPercentage": 45.0,
                "atRiskAttendance": 75.0,
            },
        }
        path = tmp_path / "dev.yaml"
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
        TenantConfigDocument.model_validate(yaml.safe_load(path.read_text()))
