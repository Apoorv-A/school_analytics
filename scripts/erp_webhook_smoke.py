#!/usr/bin/env python3
"""Send a signed POST /api/erp/import for local or staging smoke tests."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx

from app.erp_webhook_security import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    compute_signature,
)

_MAX_BUNDLE_BYTES = 50 * 1024 * 1024


def _build_multipart_body(
    *,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> tuple[bytes, str]:
    boundary = "----SchoolAnalyticsWebhookBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _signed_headers(signing_key: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = f"smoke-{uuid.uuid4().hex}"
    signature = compute_signature(signing_key, timestamp, nonce, body)
    return {
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: signature,
    }


def _resolve_bundle(path: Path) -> tuple[str, bytes, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise SystemExit(f"Bundle not found: {resolved}")
    if resolved.stat().st_size > _MAX_BUNDLE_BYTES:
        raise SystemExit(f"Bundle exceeds {_MAX_BUNDLE_BYTES} bytes: {resolved}")
    suffix = resolved.suffix.lower()
    if suffix == ".zip":
        content_type = "application/zip"
    elif suffix == ".csv":
        content_type = "text/csv"
    else:
        content_type = "application/octet-stream"
    return resolved.name, resolved.read_bytes(), content_type


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        required=True,
        help="Origin including scheme, e.g. http://sunrise.localhost:8000",
    )
    parser.add_argument(
        "--bundle",
        required=True,
        type=Path,
        help="Path to ZIP or CSV import bundle",
    )
    parser.add_argument(
        "--secret-env",
        default="SA_SECRET_SUNRISE_ERP_WEBHOOK",
        help="Environment variable holding the webhook signing secret",
    )
    parser.add_argument(
        "--host",
        default="",
        help="Optional Host header override (defaults to URL host)",
    )
    args = parser.parse_args(argv)

    signing_key = os.environ.get(args.secret_env, "").strip()
    if not signing_key:
        raise SystemExit(f"Set {args.secret_env} to the tenant signing secret.")

    filename, content, content_type = _resolve_bundle(args.bundle)
    body, multipart_type = _build_multipart_body(
        filename=filename,
        content=content,
        content_type=content_type,
    )
    headers = {
        "Content-Type": multipart_type,
        **_signed_headers(signing_key, body),
    }
    if args.host:
        headers["Host"] = args.host.strip()

    url = args.base_url.rstrip("/") + "/api/erp/import"
    with httpx.Client(timeout=120.0) as client:
        response = client.post(url, content=body, headers=headers)

    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = {"raw": response.text[:2000]}

    print(json.dumps({"status_code": response.status_code, "body": payload}, indent=2))
    return 0 if response.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
