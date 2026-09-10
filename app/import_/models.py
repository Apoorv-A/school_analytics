"""Pydantic row models for CSV ingest."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SchoolStructureRow(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    grade_level: int = Field(ge=1, le=12)
    grade_name: str = Field(min_length=1, max_length=60)
    section_name: str = Field(min_length=1, max_length=10)
    subject_code: str = Field(min_length=1, max_length=16)
    subject_name: str = Field(min_length=1, max_length=80)


class UserRow(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    role: str = Field(pattern=r"^(admin|teacher|parent|student)$")
    admission_no: str | None = Field(default=None, max_length=32)
    section_code: str | None = Field(default=None, max_length=32)
    subject_codes: str | None = Field(default=None, max_length=200)
    external_id: str | None = Field(default=None, max_length=64)
    source_system: str | None = Field(default=None, max_length=40)


class ScoreRow(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    admission_no: str = Field(min_length=1, max_length=32)
    assessment_code: str = Field(min_length=1, max_length=64)
    marks_obtained: float | None = None
    is_absent: bool = False
    external_id: str | None = Field(default=None, max_length=64)
    source_system: str | None = Field(default=None, max_length=40)


class AttendanceRow(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    admission_no: str = Field(min_length=1, max_length=32)
    on_date: date
    status: str = Field(pattern=r"^(present|absent|late|excused)$")
    external_id: str | None = Field(default=None, max_length=64)
    source_system: str | None = Field(default=None, max_length=40)


class RemarkRow(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    admission_no: str = Field(min_length=1, max_length=32)
    teacher_email: EmailStr | None = None
    category: str = Field(pattern=r"^(academic|behaviour|achievement|concern)$")
    body: str = Field(min_length=1, max_length=4000)
    term_sequence: int | None = Field(default=None, ge=1, le=6)
    subject_code: str | None = Field(default=None, max_length=16)
    external_id: str | None = Field(default=None, max_length=64)
    source_system: str | None = Field(default=None, max_length=40)
