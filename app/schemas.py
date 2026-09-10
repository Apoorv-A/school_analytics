"""Pydantic models validating every piece of external input before it reaches the ORM."""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.models import AssessmentType, RemarkCategory

_MAX_SUBJECT_FILTERS = 5
_MAX_SECTION_FILTERS = 4

# Identifiers arriving from the client are bounded so a malformed or hostile value
# is rejected by validation rather than reaching a query.
_MIN_ID = 1
_MAX_ID = 2_000_000_000
_ID = Field(default=None, ge=_MIN_ID, le=_MAX_ID)
_ID_LIST = Field(default=None, max_length=5)


class LoginForm(BaseModel):
    """Validated login credentials. Email normalisation happens here, not in the route."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class FilterParams(BaseModel):
    """The shared dashboard filter bar, validated as a unit."""

    model_config = ConfigDict(extra="forbid")

    academic_year_id: int | None = _ID
    term_id: int | None = _ID
    subject_id: int | None = _ID
    subject_ids: list[int] | None = _ID_LIST
    grade_id: int | None = _ID
    section_id: int | None = _ID
    section_ids: list[int] | None = Field(default=None, max_length=4)
    student_id: int | None = _ID
    teacher_id: int | None = _ID
    assessment_type: AssessmentType | None = None
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("subject_ids", "section_ids")
    @classmethod
    def _unique_positive_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        cleaned = [item for item in value if item is not None]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("duplicate ids are not allowed")
        for item in cleaned:
            if item < _MIN_ID or item > _MAX_ID:
                raise ValueError(f"id must be between {_MIN_ID} and {_MAX_ID}")
        return cleaned

    @model_validator(mode="after")
    def _check_date_window(self) -> FilterParams:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self

    def resolved_subject_ids(self) -> list[int] | None:
        if self.subject_ids:
            return self.subject_ids
        if self.subject_id is not None:
            return [self.subject_id]
        return None

    def resolved_section_ids(self) -> list[int] | None:
        if self.section_ids:
            return self.section_ids
        if self.section_id is not None:
            return [self.section_id]
        return None

    def with_student(self, student_id: int) -> FilterParams:
        return self.model_copy(update={"student_id": student_id})

    def with_section(self, section_id: int) -> FilterParams:
        return self.model_copy(update={"section_id": section_id, "section_ids": None})


def filter_params(
    academic_year_id: int | None = Query(default=None, ge=1),
    term_id: int | None = Query(default=None, ge=1),
    subject_id: int | None = Query(default=None, ge=1),
    subject_ids: list[int] | None = Query(default=None),
    grade_id: int | None = Query(default=None, ge=1),
    section_id: int | None = Query(default=None, ge=1),
    section_ids: list[int] | None = Query(default=None),
    student_id: int | None = Query(default=None, ge=1),
    teacher_id: int | None = Query(default=None, ge=1),
    assessment_type: AssessmentType | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> FilterParams:
    """FastAPI dependency turning query params into a validated `FilterParams`."""
    try:
        return FilterParams(
            academic_year_id=academic_year_id,
            term_id=term_id,
            subject_id=subject_id,
            subject_ids=subject_ids,
            grade_id=grade_id,
            section_id=section_id,
            section_ids=section_ids,
            student_id=student_id,
            teacher_id=teacher_id,
            assessment_type=assessment_type,
            date_from=date_from,
            date_to=date_to,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[error["msg"] for error in exc.errors()],
        ) from exc


class FilterOption(BaseModel):
    id: int | str
    label: str
    grade_id: int | None = None


class FilterOptions(BaseModel):
    """Dropdown contents for the filter bar, already narrowed to the caller's scope."""

    academic_years: list[FilterOption] = []
    terms: list[FilterOption] = []
    subjects: list[FilterOption] = []
    grades: list[FilterOption] = []
    sections: list[FilterOption] = []
    students: list[FilterOption] = []
    assessment_types: list[FilterOption] = []


class StudentSearchResult(BaseModel):
    id: int
    label: str
    admission_no: str
    section_label: str


class RemarkCreate(BaseModel):
    """Teacher remark for one assigned student."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    student_id: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=4000)
    category: RemarkCategory = RemarkCategory.ACADEMIC
    term_id: int | None = Field(default=None, ge=1)
    subject_id: int | None = Field(default=None, ge=1)


class KpiCard(BaseModel):
    label: str
    value: str
    hint: str | None = None
    delta: float | None = None
    tone: str = "neutral"


class ChartSeries(BaseModel):
    label: str
    data: list[float | None]
    kind: str | None = None


class ChartPayload(BaseModel):
    """Chart-ready response shared by every visualization endpoint."""

    labels: list[str] = []
    series: list[ChartSeries] = []
    meta: dict[str, object] = {}


class TableColumn(BaseModel):
    key: str
    label: str
    align: str = "left"


class TablePayload(BaseModel):
    columns: list[TableColumn] = []
    rows: list[dict[str, object]] = []
    meta: dict[str, object] = {}
