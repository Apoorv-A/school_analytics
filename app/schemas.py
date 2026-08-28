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
    model_validator,
)

from app.models import AssessmentType

# Identifiers arriving from the client are bounded so a malformed or hostile value
# is rejected by validation rather than reaching a query.
_ID = Field(default=None, ge=1, le=2_000_000_000)


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
    grade_id: int | None = _ID
    section_id: int | None = _ID
    student_id: int | None = _ID
    teacher_id: int | None = _ID
    assessment_type: AssessmentType | None = None
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _check_date_window(self) -> FilterParams:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self

    def with_student(self, student_id: int) -> FilterParams:
        return self.model_copy(update={"student_id": student_id})

    def with_section(self, section_id: int) -> FilterParams:
        return self.model_copy(update={"section_id": section_id})


def filter_params(
    academic_year_id: int | None = Query(default=None, ge=1),
    term_id: int | None = Query(default=None, ge=1),
    subject_id: int | None = Query(default=None, ge=1),
    grade_id: int | None = Query(default=None, ge=1),
    section_id: int | None = Query(default=None, ge=1),
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
            grade_id=grade_id,
            section_id=section_id,
            student_id=student_id,
            teacher_id=teacher_id,
            assessment_type=assessment_type,
            date_from=date_from,
            date_to=date_to,
        )
    except ValidationError as exc:
        # Cross-field rules such as the date window fail inside the model rather than
        # during parameter parsing, so they are surfaced as a 422 here instead of
        # escaping as a server error.
        raise HTTPException(
            status_code=422,
            detail=[error["msg"] for error in exc.errors()],
        ) from exc


class FilterOption(BaseModel):
    id: int | str
    label: str


class FilterOptions(BaseModel):
    """Dropdown contents for the filter bar, already narrowed to the caller's scope."""

    academic_years: list[FilterOption] = []
    terms: list[FilterOption] = []
    subjects: list[FilterOption] = []
    grades: list[FilterOption] = []
    sections: list[FilterOption] = []
    students: list[FilterOption] = []
    assessment_types: list[FilterOption] = []


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
