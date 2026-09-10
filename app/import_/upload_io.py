"""Shared upload extraction with size guards."""

from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi import UploadFile

from app.import_.files import FILE_MODELS
from app.import_.limits import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_TOTAL_BYTES,
    MAX_ZIP_DECOMPRESSED_BYTES,
)


def _copy_limited(src, dest, *, max_bytes: int) -> tuple[int, str | None]:
    written = 0
    while True:
        chunk = src.read(64 * 1024)
        if not chunk:
            break
        written += len(chunk)
        if written > max_bytes:
            return written, f"File exceeds {max_bytes // (1024 * 1024)} MB limit."
        dest.write(chunk)
    return written, None


def write_named_uploads(files: list[UploadFile], dest: Path) -> list[str]:
    """Persist allowed CSV uploads into dest; return validation errors."""
    errors: list[str] = []
    total = 0
    for upload in files:
        if not upload.filename:
            continue
        name = Path(upload.filename).name
        if name not in FILE_MODELS:
            errors.append(f"Unexpected file name: {name}")
            continue
        with (dest / name).open("wb") as out:
            written, err = _copy_limited(
                upload.file, out, max_bytes=MAX_UPLOAD_BYTES
            )
        if err:
            errors.append(f"{name}: {err}")
            continue
        total += written
        if total > MAX_UPLOAD_TOTAL_BYTES:
            errors.append(
                f"Upload bundle exceeds {MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)} MB total limit."
            )
            break
    return errors


def extract_archive_upload(upload: UploadFile, dest: Path) -> list[str]:
    """Extract allowed CSV members from a zip or single file upload."""
    errors: list[str] = []
    allowed = set(FILE_MODELS)
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix == ".zip":
        extracted = 0
        with zipfile.ZipFile(upload.file) as archive:
            decompressed = 0
            for member in archive.namelist():
                name = Path(member).name
                if name not in allowed:
                    continue
                info = archive.getinfo(member)
                if info.file_size > MAX_UPLOAD_BYTES:
                    errors.append(f"{name}: member exceeds per-file size limit.")
                    continue
                decompressed += info.file_size
                if decompressed > MAX_ZIP_DECOMPRESSED_BYTES:
                    errors.append(
                        "Zip exceeds decompressed size limit "
                        f"({MAX_ZIP_DECOMPRESSED_BYTES // (1024 * 1024)} MB)."
                    )
                    break
                target = dest / name
                with archive.open(member) as src, target.open("wb") as out:
                    _, err = _copy_limited(src, out, max_bytes=MAX_UPLOAD_BYTES)
                if err:
                    errors.append(f"{name}: {err}")
                else:
                    extracted += 1
                decompressed = sum(
                    path.stat().st_size for path in dest.iterdir() if path.is_file()
                )
                if decompressed > MAX_ZIP_DECOMPRESSED_BYTES:
                    errors.append(
                        "Zip exceeds decompressed size limit "
                        f"({MAX_ZIP_DECOMPRESSED_BYTES // (1024 * 1024)} MB)."
                    )
                    break
            if not errors and extracted == 0:
                errors.append("No recognized CSV files in archive.")
    else:
        name = Path(upload.filename or "").name
        if name in allowed:
            with (dest / name).open("wb") as out:
                _, err = _copy_limited(upload.file, out, max_bytes=MAX_UPLOAD_BYTES)
            if err:
                errors.append(f"{name}: {err}")
        else:
            errors.append(f"Unexpected file name: {name}")
    return errors
