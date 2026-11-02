"""Parental consent API (DPDP pilot)."""

from __future__ import annotations

from app.models import ConsentPurpose, ConsentStatus


def test_admin_records_and_queries_consent(admin_client, db, parent_context):
    from app.models import Student

    student_id = parent_context["child_id"]
    guardian_id = db.get(Student, student_id).guardian_user_id
    response = admin_client.post(
        "/api/consent/records",
        json={
            "student_id": student_id,
            "purpose": ConsentPurpose.STUDENT_ANALYTICS.value,
            "status": ConsentStatus.GRANTED.value,
            "privacy_notice_version": "2026-01",
            "guardian_user_id": guardian_id,
            "dpa_reference": "DPA-SUNRISE-2026",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == ConsentStatus.GRANTED.value
    assert body["privacy_notice_version"] == "2026-01"

    status_resp = admin_client.get(f"/api/consent/students/{student_id}")
    assert status_resp.status_code == 200
    purposes = {row["purpose"]: row for row in status_resp.json()["purposes"]}
    assert purposes[ConsentPurpose.STUDENT_ANALYTICS.value]["status"] == "granted"
    assert purposes[ConsentPurpose.DATA_PROCESSING.value]["status"] is None


def test_parent_records_for_own_child_only(parent_context, admin_client):
    client = parent_context["client"]
    child_id = parent_context["child_id"]
    other_id = parent_context["other_student_id"]

    denied = client.post(
        "/api/consent/records",
        json={
            "student_id": other_id,
            "purpose": ConsentPurpose.DATA_PROCESSING.value,
            "status": ConsentStatus.GRANTED.value,
            "privacy_notice_version": "2026-01",
        },
    )
    assert denied.status_code == 404

    granted = client.post(
        "/api/consent/records",
        json={
            "student_id": child_id,
            "purpose": ConsentPurpose.DATA_PROCESSING.value,
            "status": ConsentStatus.GRANTED.value,
            "privacy_notice_version": "2026-01",
        },
    )
    assert granted.status_code == 201

    me = client.get("/api/consent/guardians/me")
    assert me.status_code == 200
    students = {row["student_id"]: row for row in me.json()["students"]}
    assert child_id in students
    dp = next(
        p
        for p in students[child_id]["purposes"]
        if p["purpose"] == ConsentPurpose.DATA_PROCESSING.value
    )
    assert dp["status"] == ConsentStatus.GRANTED.value


def test_withdrawal_updates_latest_status(admin_client, parent_context):
    student_id = parent_context["child_id"]
    for status in (ConsentStatus.GRANTED, ConsentStatus.WITHDRAWN):
        admin_client.post(
            "/api/consent/records",
            json={
                "student_id": student_id,
                "purpose": ConsentPurpose.STUDENT_ANALYTICS.value,
                "status": status.value,
                "privacy_notice_version": "2026-02",
            },
        )
    row = admin_client.get(f"/api/consent/students/{student_id}").json()
    analytics = next(
        p
        for p in row["purposes"]
        if p["purpose"] == ConsentPurpose.STUDENT_ANALYTICS.value
    )
    assert analytics["status"] == ConsentStatus.WITHDRAWN.value


def test_anonymous_consent_is_401(anon_client, parent_context):
    student_id = parent_context["child_id"]
    assert anon_client.get(f"/api/consent/students/{student_id}").status_code == 401
