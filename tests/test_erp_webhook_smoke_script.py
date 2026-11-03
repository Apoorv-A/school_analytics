"""Smoke script helpers for signed ERP webhook posts."""

from __future__ import annotations

from pathlib import Path

from scripts.erp_webhook_smoke import _build_multipart_body, _signed_headers


def test_smoke_script_builds_signature_headers():
    body, content_type = _build_multipart_body(
        filename="scores.csv",
        content=b"admission_no\n",
        content_type="text/csv",
    )
    assert "multipart/form-data" in content_type
    headers = _signed_headers("test-secret", body)
    assert headers["X-SA-Webhook-Timestamp"]
    assert headers["X-SA-Webhook-Nonce"].startswith("smoke-")
    assert headers["X-SA-Webhook-Signature"].startswith("v1=")


def test_smoke_script_rejects_missing_bundle(tmp_path: Path):
    from scripts import erp_webhook_smoke

    missing = tmp_path / "nope.zip"
    try:
        erp_webhook_smoke._resolve_bundle(missing)
    except SystemExit as exc:
        assert "not found" in str(exc).lower()
    else:
        raise AssertionError("expected SystemExit")
