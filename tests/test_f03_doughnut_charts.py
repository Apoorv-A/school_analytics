"""F03 slice: composition doughnuts and pass-rate gauge totals."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.analytics.charts import _composition_doughnut
from app.models import Term
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
