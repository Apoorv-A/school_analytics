"""Tenant policy guards for family portals and attendance rules.

Policies are resolved from ``settings_json`` at request time and applied in chart
builders, portal page assembly, and the chart API.
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.consent.status import student_analytics_consent_granted
from app.deps import AccessScope
from app.models import AttendanceStatus, Role
from app.schemas import FilterParams
from app.tenant.context import require_tenant_context
from app.tenant.settings import TenantSettings

COHORT_ONLY_CARD_KEYS = frozenset({"student.vs_class"})

COHORT_ONLY_CHART_KEYS = frozenset({"student.vs_class"})

INTERVENTION_CHART_KEYS = frozenset(
    {
        "staff.intervention_uncovered",
        "staff.interventions_open",
        "staff.intervention_outcomes",
    }
)

REMARKS_CHART_KEYS = frozenset({"student.remarks"})

INSIGHT_SUMMARY_CHART_KEY = "student.insight_summary"

HIDDEN_FAMILY_KPI_LABELS = frozenset({"Versus class average", "Class rank"})


class FamilyComparisonMode(StrEnum):
    SELF = "self"
    COHORT = "cohort"


def family_comparison_mode() -> FamilyComparisonMode:
    raw = require_tenant_context().settings.family_comparison_mode
    try:
        return FamilyComparisonMode(raw)
    except ValueError:
        return FamilyComparisonMode.SELF


def cohort_benchmarks_visible(scope: AccessScope) -> bool:
    """Whether parent/student views may show class rank and cohort benchmarks."""
    if scope.role not in (Role.PARENT, Role.STUDENT):
        return True
    return family_comparison_mode() is FamilyComparisonMode.COHORT


def _consent_gates_insight_summary(scope: AccessScope) -> bool:
    settings = require_tenant_context().settings
    return settings.parental_consent_enabled and scope.role in (Role.PARENT, Role.STUDENT)


def family_insight_summary_visible(
    scope: AccessScope, db: Session, student_id: int
) -> bool:
    if not _consent_gates_insight_summary(scope):
        return True
    return student_analytics_consent_granted(db, scope.tenant_id, student_id)


def filter_portal_cards(
    cards: list[dict],
    scope: AccessScope,
    *,
    db: Session | None = None,
    student_id: int | None = None,
) -> list[dict]:
    """Drop cohort-only and consent-gated dashboard cards for family portals."""
    filtered = cards
    if not cohort_benchmarks_visible(scope):
        filtered = [
            card for card in filtered if card.get("key") not in COHORT_ONLY_CARD_KEYS
        ]
    if (
        db is not None
        and student_id is not None
        and _consent_gates_insight_summary(scope)
        and not family_insight_summary_visible(scope, db, student_id)
    ):
        filtered = [
            card for card in filtered if card.get("key") != INSIGHT_SUMMARY_CHART_KEY
        ]
    return filtered


def guard_cohort_chart(chart_key: str, scope: AccessScope) -> None:
    if chart_key in COHORT_ONLY_CHART_KEYS and not cohort_benchmarks_visible(scope):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def chart_allowed_for_scope(chart_key: str, scope: AccessScope) -> bool:
    if chart_key in COHORT_ONLY_CHART_KEYS and not cohort_benchmarks_visible(scope):
        return False
    if chart_key in INTERVENTION_CHART_KEYS:
        return require_tenant_context().settings.interventions_enabled
    if chart_key in REMARKS_CHART_KEYS:
        return require_tenant_context().settings.remarks_enabled
    return True


def guard_analytics_consent(
    chart_key: str,
    db: Session,
    scope: AccessScope,
    filters: FilterParams,
) -> None:
    """Block parent/student insight summary when analytics consent is not granted."""
    if chart_key != INSIGHT_SUMMARY_CHART_KEY:
        return
    if not _consent_gates_insight_summary(scope):
        return
    student_id = filters.student_id
    if student_id is None and scope.student_ids is not None and len(scope.student_ids) == 1:
        student_id = next(iter(scope.student_ids))
    if student_id is None or not student_analytics_consent_granted(
        db, scope.tenant_id, student_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def attendance_present_days(counts: dict[str, int], settings: TenantSettings) -> int:
    """Count days that contribute to attendance percentage under tenant policy."""
    present = counts.get(AttendanceStatus.PRESENT.value, 0)
    if settings.attendance_count_late_as_present:
        present += counts.get(AttendanceStatus.LATE.value, 0)
    if settings.attendance_count_excused_as_present:
        present += counts.get(AttendanceStatus.EXCUSED.value, 0)
    return present


def sanitize_family_chart(chart_key: str, payload: dict, scope: AccessScope) -> dict:
    """Strip cohort benchmarks from chart payloads when family mode is self."""
    if cohort_benchmarks_visible(scope):
        return payload

    if chart_key == "student.kpis":
        cards = payload.get("cards") or []
        payload["cards"] = [
            card for card in cards if card.get("label") not in HIDDEN_FAMILY_KPI_LABELS
        ]
        return payload

    if chart_key == "student.subject_radar":
        series = payload.get("series") or []
        payload["series"] = [
            entry for entry in series if entry.get("label") != "Class average"
        ]
        return payload

    if chart_key == "student.term_progress":
        series = payload.get("series") or []
        payload["series"] = [
            entry for entry in series if entry.get("label") != "Class average"
        ]
        meta = dict(payload.get("meta") or {})
        rows = []
        for row in meta.get("rows") or []:
            rows.append(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"rank", "cohort", "percentile", "class_avg"}
                }
            )
        meta["rows"] = rows
        payload["meta"] = meta
        return payload

    if chart_key == "student.subject_table":
        hidden = {"class_avg", "gap", "rank", "percentile"}
        columns = [
            column
            for column in (payload.get("columns") or [])
            if column.get("key") not in hidden
        ]
        rows = []
        for row in payload.get("rows") or []:
            rows.append({key: value for key, value in row.items() if key not in hidden})
        payload["columns"] = columns
        payload["rows"] = rows
        return payload

    if chart_key == "student.subject_trend":
        series = payload.get("series") or []
        payload["series"] = [
            entry for entry in series if entry.get("label") != "Class average"
        ]
        return payload

    if chart_key == "student.assessment_table":
        hidden = {"class_avg", "gap"}
        columns = [
            column
            for column in (payload.get("columns") or [])
            if column.get("key") not in hidden
        ]
        rows = []
        for row in payload.get("rows") or []:
            rows.append({key: value for key, value in row.items() if key not in hidden})
        payload["columns"] = columns
        payload["rows"] = rows
        return payload

    return payload
