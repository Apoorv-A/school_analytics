"""F01-04: portal nav to metrics appendix and chart info panels."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Role, Tenant
from app.portals import portal_nav
from app.tenant.settings import TenantSettings
from seed.generate import SUNRISE_HOST

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


def test_metrics_guide_in_portal_nav_all_roles(db):
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    settings = TenantSettings.from_json(tenant.settings_json)
    for role in (Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT):
        hrefs = [
            item["href"] for group in portal_nav(role, settings) for item in group["items"]
        ]
        assert "/help/metrics" in hrefs


def test_admin_overview_links_metrics_guide(admin_client):
    response = admin_client.get("/admin", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    assert "/help/metrics" in response.text
    assert "Metrics guide" in response.text
    assert 'aria-label="About this chart"' in response.text
    assert "chart_info.js" in response.text


def test_metrics_appendix_highlights_nav(admin_client):
    response = admin_client.get("/help/metrics", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    assert 'href="/help/metrics"' in response.text
    assert 'aria-current="page"' in response.text


def test_chart_info_requires_auth(anon_client):
    response = anon_client.get("/api/charts/school.kpis/info", headers=DEFAULT_HEADERS)
    assert response.status_code == 401


def test_chart_info_unknown_chart_404(admin_client):
    response = admin_client.get(
        "/api/charts/school.not_a_chart/info", headers=DEFAULT_HEADERS
    )
    assert response.status_code == 404


def test_chart_info_admin_includes_staff_fields(admin_client):
    response = admin_client.get("/api/charts/school.kpis/info", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["key"] == "school.kpis"
    assert body["title"]
    assert body["metrics"]
    assert any("implementation" in m for m in body["metrics"])
    assert body["appendixHref"] == "/help/metrics"


def test_chart_info_family_omits_implementation(parent_context):
    response = parent_context["client"].get(
        "/api/charts/student.kpis/info", headers=DEFAULT_HEADERS
    )
    assert response.status_code == 200
    for metric in response.json()["metrics"]:
        assert "implementation" not in metric


@pytest.mark.parametrize(
    "role_fixture,chart_key",
    [
        ("teacher_context", "section.kpis"),
        ("student_context", "student.kpis"),
    ],
)
def test_chart_info_respects_role(request, role_fixture, chart_key):
    fixture = request.getfixturevalue(role_fixture)
    client = fixture["client"]
    response = client.get(f"/api/charts/{chart_key}/info", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    assert response.json()["key"] == chart_key


def test_chart_info_forbidden_chart_404(parent_context):
    response = parent_context["client"].get(
        "/api/charts/school.kpis/info", headers=DEFAULT_HEADERS
    )
    assert response.status_code == 404
