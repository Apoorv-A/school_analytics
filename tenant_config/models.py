"""Canonical tenant configuration models (code repo source of truth)."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TenantEnvironment(str, Enum):
    DEV = "dev"
    STG = "stg"
    PROD = "prod"


class TenantIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9-]+$")
    displayName: str = Field(min_length=1, max_length=160)
    hostname: str = Field(min_length=3, max_length=255)
    environment: TenantEnvironment


class SchoolDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    board: str | None = Field(default=None, max_length=60)


class AcademicThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passPercentage: float = Field(ge=0, le=100)
    atRiskPercentage: float = Field(ge=0, le=100)
    atRiskAttendance: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _at_risk_above_pass(self) -> AcademicThresholds:
        if self.atRiskPercentage < self.passPercentage:
            raise ValueError("atRiskPercentage must not be below passPercentage")
        return self


class FeatureFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exports: bool = True
    remarks: bool = True
    parentPortal: bool = True
    studentPortal: bool = True
    bulkImport: bool = False
    interventions: bool = False


class AuthPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mfaRequiredRoles: list[str] = Field(default_factory=lambda: ["admin", "teacher"])
    sessionMaxAge: int = Field(default=28800, ge=300, le=86400)


class DemoDataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    teacherEmail: str | None = None
    parentEmail: str | None = None
    studentEmail: str | None = None


class TenantConfigDocument(BaseModel):
    """One tenant in one environment."""

    model_config = ConfigDict(extra="forbid")

    tenant: TenantIdentity
    school: SchoolDetails
    academics: AcademicThresholds
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    auth: AuthPolicy = Field(default_factory=AuthPolicy)
    demoData: DemoDataConfig | None = None

    @model_validator(mode="after")
    def _prod_rules(self) -> TenantConfigDocument:
        if self.tenant.environment is TenantEnvironment.PROD:
            if self.demoData is not None and self.demoData.enabled:
                raise ValueError("demoData is not allowed in production")
            if "admin" not in self.auth.mfaRequiredRoles:
                raise ValueError("production requires admin in mfaRequiredRoles")
        return self


class EnvironmentRelease(BaseModel):
    """Shared application release for one environment."""

    model_config = ConfigDict(extra="forbid")

    environment: TenantEnvironment
    imageTag: str = Field(min_length=1)
    operatorTag: str | None = None

    @field_validator("imageTag")
    @classmethod
    def _no_latest(cls, value: str) -> str:
        if value == "latest":
            raise ValueError("imageTag must not be 'latest'")
        return value
