"""Smoke script helpers for local REST export server."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from scripts.rest_export_smoke_server import build_export_zip


def test_build_export_zip_includes_csv_templates() -> None:
    root = Path(__file__).resolve().parents[1]
    templates = root / "docs/onboarding/csv-templates"
    payload = build_export_zip(templates)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = set(archive.namelist())
    assert "school_structure.csv" in names
    assert "scores.csv" in names
