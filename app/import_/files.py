"""Allowed CSV bundle filenames and row models."""

from __future__ import annotations

from app.import_.models import (
    AttendanceRow,
    RemarkRow,
    SchoolStructureRow,
    ScoreRow,
    UserRow,
)

FILE_MODELS: dict[str, type] = {
    "school_structure.csv": SchoolStructureRow,
    "users.csv": UserRow,
    "scores.csv": ScoreRow,
    "attendance.csv": AttendanceRow,
    "remarks.csv": RemarkRow,
}
