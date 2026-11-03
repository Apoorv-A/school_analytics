#!/usr/bin/env python3
"""Serve a sample ERP export zip for local REST connector smoke tests."""

from __future__ import annotations

import argparse
import io
import os
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATES = ROOT / "docs/onboarding/csv-templates"
DEFAULT_EXPORT_PATH = "/export.zip"


def build_export_zip(source_dir: Path) -> bytes:
    """Zip recognized CSV templates from a directory."""
    if not source_dir.is_dir():
        raise SystemExit(f"CSV template directory not found: {source_dir}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.glob("*.csv")):
            archive.writestr(path.name, path.read_bytes())
    payload = buffer.getvalue()
    if not payload:
        raise SystemExit(f"No CSV files under {source_dir}")
    return payload


def _expected_bearer(token: str) -> str:
    return f"Bearer {token.strip()}"


class ExportHandler(BaseHTTPRequestHandler):
    export_path: str = DEFAULT_EXPORT_PATH
    zip_bytes: bytes = b""
    bearer_token: str = ""

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != self.export_path:
            self.send_error(404, "Not found")
            return
        auth = self.headers.get("Authorization", "").strip()
        if auth != _expected_bearer(self.bearer_token):
            self.send_error(401, "Unauthorized")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{Path(self.export_path).name}"',
        )
        self.send_header("Content-Length", str(len(self.zip_bytes)))
        self.end_headers()
        self.wfile.write(self.zip_bytes)

    def log_message(self, format: str, *args: object) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Listen port (default 8765)",
    )
    parser.add_argument(
        "--export-path",
        default=DEFAULT_EXPORT_PATH,
        help=f"URL path to serve (default {DEFAULT_EXPORT_PATH})",
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=DEFAULT_TEMPLATES,
        help="Directory of CSV files to zip",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("REST_SMOKE_BEARER_TOKEN", "smoke-bearer-token"),
        help="Expected bearer token (or set REST_SMOKE_BEARER_TOKEN)",
    )
    args = parser.parse_args(argv)

    token = args.token.strip()
    if not token:
        raise SystemExit("Bearer token must not be empty.")

    zip_bytes = build_export_zip(args.templates_dir.resolve())
    handler_cls = type(
        "ConfiguredExportHandler",
        (ExportHandler,),
        {
            "export_path": args.export_path,
            "zip_bytes": zip_bytes,
            "bearer_token": token,
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    print(
        f"Serving REST export on http://{args.host}:{args.port}{args.export_path} "
        f"({len(zip_bytes)} byte zip, bearer required)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
