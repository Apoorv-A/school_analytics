"""Parental consent pilot UI and analytics gating."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models import ConsentPurpose, ConsentStatus, Tenant


@pytest.fixture(autouse=True)
def _reset_sunrise_parental_consent(db):
    """These tests toggle features.parentalConsent; restore seed defaults after each."""
    yield
    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    changed = False
    if features.pop("parentalConsent", None) is not None:
        settings["features"] = features
        changed = True
    if "consent" in settings:
        settings.pop("consent")
        changed = True
    if changed:
        tenant.settings_json = settings
        flag_modified(tenant, "settings_json")
        db.commit()


def _enable_consent_pilot(db, *, tenant_key: str = "sunrise") -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    features["parentalConsent"] = True
    settings["features"] = features
    consent = dict(settings.get("consent") or {})
    consent["privacyNoticeVersion"] = "2026-pilot"
    settings["consent"] = consent
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


def test_consent_nav_hidden_on_parent_pages_by_default(parent_context):
    response = parent_context["client"].get("/parent/progress")
    assert response.status_code == 200
    assert "/parent/consent" not in response.text


def test_consent_nav_on_parent_progress_when_enabled(parent_context, db):
    _enable_consent_pilot(db)
    response = parent_context["client"].get("/parent/progress")
    assert response.status_code == 200
    assert "/parent/consent" in response.text
    assert "Privacy" in response.text


def test_consent_page_hidden_without_feature_flag(parent_context):
    response = parent_context["client"].get("/parent/consent")
    assert response.status_code == 404


def test_consent_page_renders_when_enabled(parent_context, db):
    _enable_consent_pilot(db)
    response = parent_context["client"].get("/parent/consent")
    assert response.status_code == 200
    assert "Privacy" in response.text and "consent" in response.text.lower()
    assert "2026-pilot" in response.text


def test_insight_summary_gated_until_analytics_consent(parent_context, db, admin_client):
    _enable_consent_pilot(db)
    child_id = parent_context["child_id"]
    client = parent_context["client"]

    overview = client.get("/parent", params={"student_id": child_id})
    assert overview.status_code == 200
    keys = set(re.findall(r'data-chart="([^"]+)"', overview.text))
    assert "student.insight_summary" not in keys

    chart = client.get(
        "/api/charts/student.insight_summary",
        params={"student_id": child_id},
    )
    assert chart.status_code == 404

    grant = client.post(
        "/api/consent/records",
        json={
            "student_id": child_id,
            "purpose": ConsentPurpose.STUDENT_ANALYTICS.value,
            "status": ConsentStatus.GRANTED.value,
            "privacy_notice_version": "2026-pilot",
        },
    )
    assert grant.status_code == 201

    overview2 = client.get("/parent", params={"student_id": child_id})
    keys2 = set(re.findall(r'data-chart="([^"]+)"', overview2.text))
    assert "student.insight_summary" in keys2

    chart2 = client.get(
        "/api/charts/student.insight_summary",
        params={"student_id": child_id},
    )
    assert chart2.status_code == 200
    assert chart2.json()["kind"] == "insight_summary"
