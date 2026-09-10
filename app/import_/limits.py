"""Upload and archive size limits for CSV ingest."""

from __future__ import annotations

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024
MAX_ZIP_DECOMPRESSED_BYTES = 50 * 1024 * 1024
