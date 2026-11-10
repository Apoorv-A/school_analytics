"""F01-03: versioned metric registry and glossary API."""

from __future__ import annotations

import pytest

from app.analytics.metric_registry import METRICS, REGISTRY_VERSION
from seed.generate import SUNRISE_HOST

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


def test_metrics_api_requires_auth(anon_client):
    response = anon_client.get("/api/metrics", headers=DEFAULT_HEADERS)
    assert response.status_code == 401


def test_metrics_list_admin_includes_registry_version(admin_client):
    response = admin_client.get("/api/metrics", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["registryVersion"] == REGISTRY_VERSION
    assert body["count"] == len(body["metrics"])
    assert body["count"] >= len(METRICS)
    ids = {item["id"] for item in body["metrics"]}
    assert "perf.cohort_weighted_average" in ids
    assert "perf.assessment_pass_rate" in ids


def test_metrics_detail_admin_has_staff_fields(admin_client):
    response = admin_client.get(
        "/api/metrics/perf.cohort_weighted_average", headers=DEFAULT_HEADERS
    )
    assert response.status_code == 200
    metric = response.json()["metric"]
    assert metric["id"] == "perf.cohort_weighted_average"
    assert metric["implementation"]
    assert metric["chartKeys"]


def test_metrics_detail_unknown_is_404(admin_client):
    response = admin_client.get("/api/metrics/not.a.real.key", headers=DEFAULT_HEADERS)
    assert response.status_code == 404


def test_metrics_staff_only_hidden_from_family(parent_context, student_context):
    for ctx in (parent_context, student_context):
        response = ctx["client"].get("/api/metrics", headers=DEFAULT_HEADERS)
        assert response.status_code == 200
        ids = {m["id"] for m in response.json()["metrics"]}
        assert "perf.assessment_results_count" not in ids
        detail = ctx["client"].get(
            "/api/metrics/perf.cohort_weighted_average", headers=DEFAULT_HEADERS
        )
        assert detail.status_code == 200
        assert "implementation" not in detail.json()["metric"]


def test_metrics_search_query(admin_client):
    response = admin_client.get(
        "/api/metrics", params={"q": "attendance"}, headers=DEFAULT_HEADERS
    )
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics
    assert all(
        "attendance" in (m["id"] + m["label"] + m["summary"]).lower() for m in metrics
    )


def test_metrics_appendix_html(admin_client):
    response = admin_client.get("/help/metrics", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    assert "chart guide" in response.text.lower()
    assert REGISTRY_VERSION in response.text
    assert 'class="metric-guide"' in response.text


@pytest.mark.parametrize(
    "role_fixture",
    ["admin_client", "teacher_context", "parent_context", "student_context"],
)
def test_all_authenticated_roles_can_list(request, role_fixture):
    fixture = request.getfixturevalue(role_fixture)
    client = fixture["client"] if isinstance(fixture, dict) else fixture
    response = client.get("/api/metrics", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    assert response.json()["count"] >= 1
