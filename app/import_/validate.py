"""Dry-run CSV validation."""

from __future__ import annotations

import csv
import tempfile
import uuid
from pathlib import Path

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.import_.files import FILE_MODELS
from app.import_.upload_io import write_named_uploads


def _validate_file(path: Path, model: type) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            errors.append({"file": path.name, "row": "0", "error": "Missing header row"})
            return errors
        for index, row in enumerate(reader, start=2):
            try:
                model.model_validate(row)
            except ValidationError as exc:
                errors.append(
                    {
                        "file": path.name,
                        "row": str(index),
                        "error": "; ".join(error["msg"] for error in exc.errors()),
                    }
                )
    return errors


def validate_directory(directory: Path) -> dict[str, object]:
    errors: list[dict[str, str]] = []
    summary: dict[str, int] = {}
    for filename, model in FILE_MODELS.items():
        path = directory / filename
        if not path.exists():
            continue
        file_errors = _validate_file(path, model)
        errors.extend(file_errors)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = sum(1 for _ in csv.DictReader(handle))
        summary[filename] = rows
    if not summary:
        errors.append(
            {
                "file": "",
                "row": "0",
                "error": "No recognized CSV files found in the bundle.",
            }
        )
    return {
        "valid": not errors,
        "errors": errors,
        "summary": summary,
    }


def validate_uploads(
    db: Session,
    tenant_id: uuid.UUID,
    files: list[UploadFile],
    *,
    user_id: int | None = None,
) -> dict[str, object]:
    from app.import_.history import record_import_run
    from app.models import ImportRunStatus

    with tempfile.TemporaryDirectory(prefix="school-import-") as tmp:
        directory = Path(tmp)
        extract_errors = write_named_uploads(files, directory)
        report = validate_directory(directory)
        if extract_errors:
            report["errors"].extend(
                {"file": "", "row": "0", "error": err} for err in extract_errors
            )
            report["valid"] = False
        record_import_run(
            db,
            tenant_id=tenant_id,
            status=ImportRunStatus.VALIDATED if report["valid"] else ImportRunStatus.FAILED,
            source="upload",
            report=report,
            user_id=user_id,
        )
        db.commit()
        return report
