"""Stable content fingerprint for ERP CSV bundles (delta sync foundation)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.import_.files import FILE_MODELS


def compute_bundle_fingerprint(directory: Path) -> str:
    """SHA-256 over recognized CSV files in deterministic order.

    Only filenames from ``FILE_MODELS`` participate. Missing files are omitted so
    partial bundles fingerprint consistently with validate/apply.
    """
    digest = hashlib.sha256()
    for filename in sorted(FILE_MODELS.keys()):
        path = directory / filename
        if not path.is_file():
            continue
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()
