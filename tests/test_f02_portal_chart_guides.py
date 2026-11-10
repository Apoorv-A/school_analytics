"""F02: Chart guides on teacher (section) and family/student portal charts."""

from __future__ import annotations

from app.analytics.charts import CHART_REGISTRY
from app.analytics.metric_registry import (
    CHART_GUIDES,
    SECTION_CHART_KEYS,
    STUDENT_CHART_KEYS,
)
from seed.generate import SUNRISE_HOST

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


def test_section_charts_have_chart_guides():
    section_keys = {key for key in CHART_REGISTRY if key.startswith("section.")}
    assert section_keys == set(SECTION_CHART_KEYS)
    for key in sorted(section_keys):
        assert key in CHART_GUIDES


def test_student_charts_have_chart_guides():
    student_keys = {key for key in CHART_REGISTRY if key.startswith("student.")}
    assert student_keys == set(STUDENT_CHART_KEYS)
    for key in sorted(student_keys):
        assert key in CHART_GUIDES


def test_chart_info_section_kpis_includes_chart_guide(teacher_context):
    response = teacher_context["client"].get(
        "/api/charts/section.kpis/info",
        headers=DEFAULT_HEADERS,
    )
    assert response.status_code == 200
    guide = response.json()["chartGuide"]
    assert "class" in guide["purpose"].lower()
    assert guide["xAxis"] == "—"
    assert "section" in guide["filterBehavior"].lower() or "class" in guide["filterBehavior"].lower()


def test_chart_info_student_subject_trend_includes_chart_guide(student_context):
    response = student_context["client"].get(
        "/api/charts/student.subject_trend/info",
        headers=DEFAULT_HEADERS,
    )
    assert response.status_code == 200
    guide = response.json()["chartGuide"]
    assert "year" in guide["purpose"].lower() or "assessment" in guide["purpose"].lower()
    assert "subject" in guide["filterBehavior"].lower()
    assert guide["aggregation"]


def test_chart_info_student_vs_class_mentions_family_mode(admin_client, parent_context):
    response = admin_client.get(
        "/api/charts/student.vs_class/info",
        headers=DEFAULT_HEADERS,
    )
    assert response.status_code == 200
    guide = response.json()["chartGuide"]
    combined = " ".join(
        guide.get(field, "") for field in ("filterBehavior", "caveats", "limitations")
    ).lower()
    assert "self-comparison" in combined

    parent_response = parent_context["client"].get(
        "/api/charts/student.kpis/info",
        headers=DEFAULT_HEADERS,
    )
    assert parent_response.status_code == 200
    parent_guide = parent_response.json()["chartGuide"]
    assert "self-comparison" in (parent_guide.get("caveats") or "").lower()
