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
        assert blocked.status_code == 404


class TestErpWebhook:
    def test_webhook_requires_signed_request(self, anon_client, db):
        from tests.test_import_apply import _enable_bulk_import

        _enable_bulk_import(db)

        response = anon_client.post(
            "/api/erp/import",
            files={"file": ("scores.csv", b"admission_no\n", "text/csv")},
        )
        assert response.status_code == 401

    def test_webhook_applies_with_valid_signature(self, anon_client, db, monkeypatch):
        from tests.test_erp_webhook import _post_signed_webhook
        from tests.test_import_apply import _enable_bulk_import, _set_erp_webhook_secret

        _enable_bulk_import(db)
        signing_key = "test-webhook-token"
        _set_erp_webhook_secret(db, signing_key, monkeypatch=monkeypatch)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "users.csv",
                "email,full_name,role\n"
                "webhook.user@example.com,Webhook User,teacher\n",
            )

        response = _post_signed_webhook(
            anon_client,
            signing_key=signing_key,
            filename="bundle.zip",
            content=buffer.getvalue(),
            content_type="application/zip",
        )
        assert response.status_code == 200
        assert response.json()["applied"] is True
