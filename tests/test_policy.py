"""Tenant policy layer: family comparison, attendance, and feature-flag routes."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models import AttendanceStatus, Role, Tenant
from app.portals import portal_nav
from app.tenant.settings import TenantSettings


def _set_family_comparison_mode(db, mode: str, *, tenant_key: str = "sunrise") -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    portal = dict(settings.get("portal") or {})
    portal["familyComparisonMode"] = mode
    settings["portal"] = portal
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


def _set_feature_flags(db, features: dict, *, tenant_key: str = "sunrise") -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    merged = dict(settings.get("features") or {})
    merged.update(features)
    settings["features"] = merged
    tenant.settings_json = settings
    flag_modified(tenant, "settings_json")
    db.commit()


def _enable_bulk_import(db, *, tenant_key: str = "sunrise") -> None:
    _set_feature_flags(db, {"bulkImport": True}, tenant_key=tenant_key)


def _chart_keys(html: str) -> set[str]:
    return set(re.findall(r'data-chart="([^"]+)"', html))


class TestFamilyComparisonPolicy:
    def test_self_mode_hides_cohort_cards_on_parent_portal(self, parent_context, db):
        _set_family_comparison_mode(db, "self")
        response = parent_context["client"].get("/parent")
        assert response.status_code == 200
        keys = _chart_keys(response.text)
        assert "student.vs_class" not in keys
        assert "student.kpis" in keys

    def test_cohort_mode_shows_cohort_cards_on_parent_portal(self, parent_context, db):
        _set_family_comparison_mode(db, "cohort")
        response = parent_context["client"].get("/parent")
        assert response.status_code == 200
        keys = _chart_keys(response.text)
        assert "student.vs_class" in keys

    def test_self_mode_blocks_vs_class_chart_api(self, parent_context, db):
        _set_family_comparison_mode(db, "self")
        response = parent_context["client"].get(
            "/api/charts/student.vs_class",
            params={"student_id": parent_context["child_id"]},
        )
        assert response.status_code == 404

    def test_cohort_mode_allows_vs_class_chart_api(self, parent_context, db):
        _set_family_comparison_mode(db, "cohort")
        response = parent_context["client"].get(
            "/api/charts/student.vs_class",
            params={"student_id": parent_context["child_id"]},
        )
        assert response.status_code == 200
        labels = [series["label"] for series in response.json()["series"]]
        assert "Class average" in labels

    def test_self_mode_strips_rank_from_kpis(self, parent_context, db):
        _set_family_comparison_mode(db, "self")
        payload = parent_context["client"].get(
            "/api/charts/student.kpis",
            params={"student_id": parent_context["child_id"]},
        ).json()
        labels = {card["label"] for card in payload["cards"]}
        assert "Class rank" not in labels
        assert "Versus class average" not in labels
        assert "Overall average" in labels

    def test_self_mode_strips_cohort_columns_from_subject_table(self, parent_context, db):
        _set_family_comparison_mode(db, "self")
        payload = parent_context["client"].get(
            "/api/charts/student.subject_table",
            params={"student_id": parent_context["child_id"]},
        ).json()
        keys = {column["key"] for column in payload["columns"]}
        assert "rank" not in keys
        assert "class_avg" not in keys
        assert "subject" in keys

    def test_staff_views_unaffected_by_self_mode(self, admin_client, parent_context, db):
        _set_family_comparison_mode(db, "self")
        student_id = parent_context["child_id"]
        payload = admin_client.get(
            "/api/charts/student.kpis", params={"student_id": student_id}
        ).json()
        labels = {card["label"] for card in payload["cards"]}
        assert "Class rank" in labels
        assert "Versus class average" in labels


class TestAttendancePolicy:
    def test_present_day_count_follows_tenant_policy(self):
        from app.tenant.policy import attendance_present_days
        from app.tenant.settings import TenantSettings

        counts = {
            AttendanceStatus.PRESENT.value: 5,
            AttendanceStatus.LATE.value: 2,
            AttendanceStatus.EXCUSED.value: 3,
            AttendanceStatus.ABSENT.value: 1,
        }
        default = TenantSettings()
        assert attendance_present_days(counts, default) == 7

        with_excused = TenantSettings(attendance_count_excused_as_present=True)
        assert attendance_present_days(counts, with_excused) == 10

        without_late = TenantSettings(attendance_count_late_as_present=False)
        assert attendance_present_days(counts, without_late) == 5

    def test_settings_default_documented_in_tenant_settings(self, db):
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        settings = TenantSettings.from_json(tenant.settings_json)
        assert settings.attendance_count_late_as_present is True
        assert settings.attendance_count_excused_as_present is False
        assert settings.family_comparison_mode == "self"


class TestFeatureFlagRouteEnforcement:
    def test_bulk_import_hidden_from_nav_when_disabled(self, db):
        _set_feature_flags(db, {"bulkImport": False, "interventions": False})
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        settings = TenantSettings.from_json(tenant.settings_json)
        hrefs = [item["href"] for group in portal_nav(Role.ADMIN, settings) for item in group["items"]]
        assert "/admin/import" not in hrefs
        assert "/admin/interventions" not in hrefs

    def test_bulk_import_nav_and_route_when_enabled(self, admin_client, db):
        _enable_bulk_import(db)
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        settings = TenantSettings.from_json(tenant.settings_json)
        hrefs = [item["href"] for group in portal_nav(Role.ADMIN, settings) for item in group["items"]]
        assert "/admin/import" in hrefs
        assert admin_client.get("/admin/import").status_code == 200

    def test_bulk_import_route_404_when_disabled(self, admin_client, db):
        _set_feature_flags(db, {"bulkImport": False})

        assert admin_client.get("/admin/import").status_code == 404
        assert admin_client.post("/admin/import/validate").status_code == 404

    def test_interventions_route_404_when_disabled(self, admin_client, db):
        _set_feature_flags(db, {"interventions": False})

        assert admin_client.get("/admin/interventions").status_code == 404
        assert admin_client.get("/api/interventions").status_code == 404

    def test_teacher_interventions_route_404_when_disabled(self, teacher_context, db):
        _set_feature_flags(db, {"interventions": False})
        assert teacher_context["client"].get("/teacher/interventions").status_code == 404

    def test_remarks_hidden_from_family_nav_when_disabled(self, db):
        _set_feature_flags(db, {"remarks": False})
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        settings = TenantSettings.from_json(tenant.settings_json)
        parent_hrefs = [
            item["href"]
            for group in portal_nav(Role.PARENT, settings)
            for item in group["items"]
        ]
        student_hrefs = [
            item["href"]
            for group in portal_nav(Role.STUDENT, settings)
            for item in group["items"]
        ]
        assert "/parent/remarks" not in parent_hrefs
        assert "/student/remarks" not in student_hrefs

    def test_remarks_routes_404_when_disabled(
        self, admin_client, parent_context, teacher_context, db
    ):
        _set_feature_flags(db, {"remarks": False})
        child_id = parent_context["child_id"]

        assert parent_context["client"].get("/parent/remarks").status_code == 404
        assert admin_client.get(f"/admin/student/{child_id}/remarks").status_code == 404
        assert (
            parent_context["client"].get(
                "/api/charts/student.remarks", params={"student_id": child_id}
            ).status_code
            == 404
        )
        assert (
            teacher_context["client"].post(
                "/api/remarks",
                json={
                    "student_id": child_id,
                    "body": "Blocked.",
                    "category": "academic",
                },
            ).status_code
            == 404
        )

    def test_exports_route_404_when_disabled(self, admin_client, db):
        _set_feature_flags(db, {"exports": False})
        assert admin_client.get("/export/school.kpis.csv").status_code == 404

    def test_exports_button_hidden_when_disabled(self, admin_client, db):
        _set_feature_flags(db, {"exports": False})
        response = admin_client.get("/admin")
        assert response.status_code == 200
        assert "data-action=\"export\"" not in response.text

    def test_parent_portal_route_404_when_disabled(self, parent_context, db):
        _set_feature_flags(db, {"parentPortal": False})
        assert parent_context["client"].get("/parent").status_code == 404

    def test_student_portal_route_404_when_disabled(self, student_context, db):
        _set_feature_flags(db, {"studentPortal": False})
        assert student_context["client"].get("/student").status_code == 404

    def test_bulk_import_api_routes_404_when_disabled(self, admin_client, db):
        _set_feature_flags(db, {"bulkImport": False})
        assert admin_client.get("/api/import/mapping-queue").status_code == 404
