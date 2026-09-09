"""Tenant configuration package."""

from tenant_config.models import (
    AcademicThresholds,
    AuthPolicy,
    EnvironmentRelease,
    FeatureFlags,
    SchoolDetails,
    TenantConfigDocument,
    TenantEnvironment,
    TenantIdentity,
)

__all__ = [
    "AcademicThresholds",
    "AuthPolicy",
    "EnvironmentRelease",
    "FeatureFlags",
    "SchoolDetails",
    "TenantConfigDocument",
    "TenantEnvironment",
    "TenantIdentity",
]
