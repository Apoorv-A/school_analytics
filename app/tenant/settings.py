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
    erp_webhook_token: str = ""
    session_max_age: int = 8 * 60 * 60
    mfa_required_roles: frozenset[str] = frozenset({"admin", "teacher"})

    @classmethod
    def from_json(cls, payload: dict | None) -> TenantSettings:
        if not payload:
            return cls()
        auth = payload.get("auth") or {}
        erp = payload.get("erp") or {}
        features = payload.get("features") or {}
        academics = payload.get("academics") or {}
        mfa_roles = auth.get("mfaRequiredRoles") or ["admin", "teacher"]
        return cls(
            pass_percentage=float(academics.get("passPercentage", 33.0)),
            at_risk_percentage=float(academics.get("atRiskPercentage", 45.0)),
            at_risk_attendance=float(academics.get("atRiskAttendance", 75.0)),
            exports_enabled=bool(features.get("exports", True)),
            remarks_enabled=bool(features.get("remarks", True)),
            parent_portal_enabled=bool(features.get("parentPortal", True)),
            student_portal_enabled=bool(features.get("studentPortal", True)),
            bulk_import_enabled=bool(features.get("bulkImport", False)),
            erp_webhook_token=str(erp.get("webhookToken") or ""),
            session_max_age=int(auth.get("sessionMaxAge", 8 * 60 * 60)),
            mfa_required_roles=frozenset(str(r) for r in mfa_roles),
        )
