"""F02: Principal trend copy, filter semantics, and chart info guides."""

from __future__ import annotations

from sqlalchemy import select

from app.analytics.charts import CHART_REGISTRY
from app.analytics.metric_registry import CHART_GUIDES, PRINCIPAL_CHART_KEYS
from app.models import Term
from seed.generate import SUNRISE_HOST

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}

TERM_TREND_SUBTITLE = (
    "Explore how average assessment performance changes across "
    "academic terms for each grade."
)


def test_admin_overview_term_trend_friendly_copy(admin_client):
    response = admin_client.get("/admin", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    text = response.text
    assert TERM_TREND_SUBTITLE in text
    assert "Ignores the term filter" not in text
    assert 'data-default-subtitle="' in text


def test_admin_overview_kpi_card_uses_content_holder_not_state_class(admin_client):
    """KPI bodies must not use .state (loading placeholder layout) as the chart holder."""
    response = admin_client.get("/admin", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    text = response.text
    kpi_start = text.index('data-chart="school.kpis"')
    kpi_slice = text[kpi_start : kpi_start + 900]
    assert 'class="content-holder" data-chart-body' in kpi_slice
    assert 'class="state" data-chart-body' not in kpi_slice


def test_term_trend_highlights_selected_term(admin_client, db):
    term_id = db.scalars(select(Term.id).order_by(Term.sequence).limit(1)).one()
    term_name = db.scalars(select(Term.name).where(Term.id == term_id)).one()
    payload = admin_client.get(
        "/api/charts/school.term_trend",
        params={"term_id": term_id},
        headers=DEFAULT_HEADERS,
    ).json()
    assert len(payload["labels"]) > 1
    assert payload["meta"]["highlightedTermIndex"] == 0
    assert payload["meta"]["highlightedTerm"] == term_name
    assert "highlighted on the full trajectory" in payload["meta"]["filterNote"]


def test_chart_info_term_trend_includes_chart_guide(admin_client):
    response = admin_client.get(
        "/api/charts/school.term_trend/info",
        headers=DEFAULT_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    guide = body["chartGuide"]
    assert TERM_TREND_SUBTITLE.startswith(guide["purpose"][:20])
    assert "highlighted" in guide["filterBehavior"].lower()
    assert body["metrics"]


def test_principal_school_charts_have_chart_guides():
    school_keys = {
        key for key in CHART_REGISTRY if key.startswith("school.")
    }
    assert school_keys == set(PRINCIPAL_CHART_KEYS)
    for key in sorted(school_keys):
        assert key in CHART_GUIDES


def test_chart_info_js_accessibility_hooks(admin_client):
    response = admin_client.get("/static/js/chart_info.js", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    text = response.text
    assert 'setAttribute("aria-labelledby", "chart-info-title")' in text
    assert "chart-info-body" in text
    assert "appendGuideSection" in text


def test_term_trajectory_metric_filter_semantics_mention_highlight(admin_client):
    response = admin_client.get(
        "/api/charts/school.term_trend/info",
        headers=DEFAULT_HEADERS,
    )
    metrics = response.json()["metrics"]
    trajectory = next(m for m in metrics if m["id"] == "perf.term_trajectory_slope")
    assert "highlighted" in trajectory["filterSemantics"].lower()
