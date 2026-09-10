"""Phase 2 parent-safe insights and ERP webhook."""

from __future__ import annotations

import io
import zipfile


class TestParentInsightSummary:
    def test_parent_can_load_summary_not_admin_insights(self, parent_context):
        child_id = parent_context["child_id"]
        client = parent_context["client"]

        summary = client.get(
            "/api/charts/student.insight_summary",
            params={"student_id": child_id},
        )
        assert summary.status_code == 200
        payload = summary.json()
        assert payload["kind"] == "insight_summary"
        assert "headline" in payload
        assert "suggestions" in payload
        assert "class_average" not in str(payload)
        assert "actions" not in payload

        blocked = client.get(
            "/api/charts/student.insights",
            params={"student_id": child_id},
        )
        assert blocked.status_code == 403


class TestErpWebhook:
    def test_webhook_requires_token(self, anon_client, db):
        from tests.test_import_apply import _enable_bulk_import

        _enable_bulk_import(db)

        response = anon_client.post(
            "/api/erp/import",
            files={"file": ("scores.csv", b"admission_no\n", "text/csv")},
        )
        assert response.status_code == 401

    def test_webhook_applies_with_valid_token(self, anon_client, db):
        from tests.test_import_apply import _enable_bulk_import, _set_erp_webhook_token

        _enable_bulk_import(db)
        token = "test-webhook-token"
        _set_erp_webhook_token(db, token)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "users.csv",
                "email,full_name,role\n"
                "webhook.user@example.com,Webhook User,teacher\n",
            )
        buffer.seek(0)

        response = anon_client.post(
            "/api/erp/import",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("bundle.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 200
        assert response.json()["applied"] is True

    def test_webhook_rejects_global_token_without_tenant_secret(
        self, anon_client, db, monkeypatch
    ):
        from app.config import get_settings
        from tests.test_import_apply import _enable_bulk_import

        _enable_bulk_import(db)
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("ERP_WEBHOOK_TOKEN", "global-only-token")
        get_settings.cache_clear()

        response = anon_client.post(
            "/api/erp/import",
            headers={"Authorization": "Bearer global-only-token"},
            files={"file": ("scores.csv", b"admission_no\n", "text/csv")},
        )
        get_settings.cache_clear()
        assert response.status_code == 401

    def test_webhook_rejects_wrong_tenant_token(self, anon_client, db):
        from tests.test_import_apply import _enable_bulk_import, _set_erp_webhook_token

        _enable_bulk_import(db)
        _set_erp_webhook_token(db, "correct-token")

        response = anon_client.post(
            "/api/erp/import",
            headers={"Authorization": "Bearer wrong-token"},
            files={"file": ("scores.csv", b"admission_no\n", "text/csv")},
        )
        assert response.status_code == 401
