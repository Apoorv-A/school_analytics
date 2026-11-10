"""F03 slice: composition doughnuts and pass-rate gauge totals."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.analytics.charts import _composition_doughnut
from app.models import (
    InterventionCase,
    InterventionPriority,
    InterventionStatus,
    Tenant,
    Term,
)
from seed.generate import SUNRISE_HOST

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}

COMPOSITION_KEYS = (
    "student.grade_mix",
    "section.grade_mix",
    "student.attendance_status",
    "school.pass_rate",
)


def _assert_composition_reconciles(payload: dict) -> None:
    segments = payload["meta"]["composition"]
    total = payload["meta"]["total"]
    assert sum(int(item["count"]) for item in segments) == total
    if total:
        share_sum = round(sum(float(item["percent"]) for item in segments), 1)
        assert 99.0 <= share_sum <= 100.1


def test_composition_helper_zero_and_single_category():
    empty = _composition_doughnut(
        labels=["Present", "Absent"],
        counts=[0, 0],
        series_label="Days",
        unit="days",
        palette="attendance",
    )
    assert empty["meta"]["total"] == 0
    assert empty["labels"] == []
    assert empty["series"][0]["data"] == []

    single = _composition_doughnut(
        labels=["A1", "A2"],
        counts=[0, 3],
        series_label="Results",
        unit="results",
        palette="grade",
    )
    assert single["meta"]["total"] == 3
    assert single["labels"] == ["A2"]
    assert single["series"][0]["data"] == [3]
    assert single["meta"]["composition"][0]["percent"] == 100.0


@pytest.mark.parametrize("key", COMPOSITION_KEYS)
def test_composition_charts_reconcile(admin_client, parent_context, key):
    params: dict[str, object] = {}
    if key.startswith("student."):
        params["student_id"] = parent_context["child_id"]
    payload = admin_client.get(f"/api/charts/{key}", params=params, headers=DEFAULT_HEADERS).json()
    assert payload["kind"] in {"doughnut", "gauge"}
    assert "composition" in payload["meta"]
    if payload["meta"]["total"]:
        _assert_composition_reconciles(payload)


def test_grade_mix_total_respects_term_filter(admin_client, parent_context, db):
    child_id = parent_context["child_id"]
    full = admin_client.get(
        "/api/charts/student.grade_mix",
        params={"student_id": child_id},
        headers=DEFAULT_HEADERS,
    ).json()
    term_id = db.scalars(select(Term.id).order_by(Term.sequence).limit(1)).one()
    narrow = admin_client.get(
        "/api/charts/student.grade_mix",
        params={"student_id": child_id, "term_id": term_id},
        headers=DEFAULT_HEADERS,
    ).json()
    assert narrow["meta"]["total"] <= full["meta"]["total"]


def test_pass_rate_composition_counts_sum_to_total(admin_client):
    payload = admin_client.get("/api/charts/school.pass_rate", headers=DEFAULT_HEADERS).json()
    if not payload["meta"]["total"]:
        assert payload["meta"].get("empty") is True
        return
    counts = [int(item["count"]) for item in payload["meta"]["composition"]]
    assert sum(counts) == payload["meta"]["total"]


def test_attendance_status_empty_window(admin_client, parent_context):
    child_id = parent_context["child_id"]
    payload = admin_client.get(
        "/api/charts/student.attendance_status",
        params={
            "student_id": child_id,
            "date_from": "2099-01-01",
            "date_to": "2099-01-31",
        },
        headers=DEFAULT_HEADERS,
    ).json()
    assert payload["meta"]["total"] == 0
    assert payload["labels"] == []


def test_charts_js_exposes_composition_helpers(admin_client):
    text = admin_client.get("/static/js/charts.js", headers=DEFAULT_HEADERS).text
    assert "composition-chart" in text
    assert "appendCompositionTable" in text
    assert "GRADE_COLORS" in text
    assert "INTERVENTION_COLORS" in text


def _enable_interventions(db, *, tenant_key: str = "sunrise") -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    features["interventions"] = True
    settings["features"] = features
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


def _disable_interventions(db, *, tenant_key: str = "sunrise") -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    features["interventions"] = False
    settings["features"] = features
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


@pytest.fixture
def interventions_enabled(db):
    _enable_interventions(db)
    yield
    _disable_interventions(db)


def test_staff_intervention_status_composition_reconciles(
    admin_client, db, interventions_enabled
):
    from tests.test_interventions import _at_risk_student_without_case

    student = _at_risk_student_without_case(db)
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    case = InterventionCase(
        tenant_id=tenant.id,
        student_id=student.id,
        signal_source="test",
        reason_summary="F03 status mix",
        priority=InterventionPriority.MEDIUM,
        status=InterventionStatus.OPEN,
    )
    db.add(case)
    db.commit()
    try:
        payload = admin_client.get(
            "/api/charts/staff.intervention_status",
            headers=DEFAULT_HEADERS,
        ).json()
        assert payload["kind"] == "doughnut"
        assert "composition" in payload["meta"]
        if payload["meta"]["total"]:
            _assert_composition_reconciles(payload)
    finally:
        db.delete(case)
        db.commit()


def test_chart_info_staff_intervention_status_includes_guide(
    admin_client, interventions_enabled
):
    response = admin_client.get(
        "/api/charts/staff.intervention_status/info",
        headers=DEFAULT_HEADERS,
    )
    assert response.status_code == 200
    guide = response.json()["chartGuide"]
    assert "status" in guide["purpose"].lower()
    assert guide["aggregation"]


def test_interventions_page_wires_status_doughnut(admin_client, interventions_enabled):
    response = admin_client.get("/admin/interventions", headers=DEFAULT_HEADERS)
    assert response.status_code == 200
    assert 'data-chart="staff.intervention_status"' in response.text
