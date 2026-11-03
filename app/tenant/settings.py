"""Per-tenant business settings resolved at request time."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TenantSettings:
    """Academic thresholds and feature flags for one tenant."""

    pass_percentage: float = 33.0
    at_risk_percentage: float = 45.0
    at_risk_attendance: float = 75.0
    exports_enabled: bool = True
    remarks_enabled: bool = True
    parent_portal_enabled: bool = True
    student_portal_enabled: bool = True
    bulk_import_enabled: bool = False
    interventions_enabled: bool = False
    family_comparison_mode: str = "self"
    attendance_count_late_as_present: bool = True
    attendance_count_excused_as_present: bool = False
    erp_webhook_secret_ref: str = ""
    erp_webhook_signing_secret_ref: str = ""
    session_max_age: int = 8 * 60 * 60
    mfa_enabled: bool = False
    mfa_required_roles: frozenset[str] = frozenset()

    @classmethod
    def from_json(cls, payload: dict | None) -> TenantSettings:
        if not payload:
            return cls()
        auth = payload.get("auth") or {}
        erp = payload.get("erp") or {}
        features = payload.get("features") or {}
        academics = payload.get("academics") or {}
        portal = payload.get("portal") or {}
        attendance = payload.get("attendance") or {}
        mfa_enabled = bool(auth.get("mfaEnabled", False))
        mfa_roles = auth.get("mfaRequiredRoles") or []
        if not mfa_enabled:
            mfa_roles = []
        return cls(
            pass_percentage=float(academics.get("passPercentage", 33.0)),
            at_risk_percentage=float(academics.get("atRiskPercentage", 45.0)),
            at_risk_attendance=float(academics.get("atRiskAttendance", 75.0)),
            exports_enabled=bool(features.get("exports", True)),
            remarks_enabled=bool(features.get("remarks", True)),
            parent_portal_enabled=bool(features.get("parentPortal", True)),
            student_portal_enabled=bool(features.get("studentPortal", True)),
            bulk_import_enabled=bool(features.get("bulkImport", False)),
            interventions_enabled=bool(features.get("interventions", False)),
            family_comparison_mode=str(portal.get("familyComparisonMode", "self")),
            attendance_count_late_as_present=bool(
                attendance.get("countLateAsPresent", True)
            ),
            attendance_count_excused_as_present=bool(
                attendance.get("countExcusedAsPresent", False)
            ),
            erp_webhook_secret_ref=str(erp.get("webhookSecretRef") or ""),
            erp_webhook_signing_secret_ref=str(erp.get("webhookSigningSecretRef") or ""),
            session_max_age=int(auth.get("sessionMaxAge", 8 * 60 * 60)),
            mfa_enabled=mfa_enabled,
            mfa_required_roles=frozenset(str(r) for r in mfa_roles),
        )
