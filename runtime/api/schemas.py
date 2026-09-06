"""Request and response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SignalIn(BaseModel):
    entity_type: Literal["account", "person"]
    entity_id: str
    type: str
    strength: float = Field(ge=0, le=1)
    half_life_h: int = Field(gt=0, default=720)
    source: str
    legal_basis: str = "legitimate_interest"
    payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime
    # Supplying this makes ingest idempotent. Its absence is allowed but
    # recorded in the response, because a source that replays without one
    # inflates every intent score it touches.
    dedupe_key: str | None = None
    score: float | None = None


class EnrollmentOut(BaseModel):
    enrollment_id: str
    program_key: str
    variant: str
    tier: str | None
    score: float | None
    reason: str


class IngestOut(BaseModel):
    accepted: bool
    deduplicated: bool
    enrollments: list[EnrollmentOut]
    warnings: list[str] = Field(default_factory=list)


class AccountIn(BaseModel):
    name: str
    domain: str | None = None
    country: str | None = None
    employee_band: str | None = None
    industry_code: str | None = None
    crm_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class PersonIn(BaseModel):
    email: str | None = None
    full_name: str | None = None
    country: str | None = None
    linkedin_urn: str | None = None
    crm_id: str | None = None
    account_id: str | None = None
    buying_role: str | None = None
    consent_state: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProgramIn(BaseModel):
    key: str
    version: str
    name: str
    spec: dict[str, Any]
    # The linter scopes some rules by blueprint. Omitting it makes the checks
    # stricter, never looser: an unknown blueprint is assumed to need human
    # review on its top tier.
    blueprint: str | None = None
    owner: str | None = None
    description: str | None = None
    activate: bool = False


class ProgramOut(BaseModel):
    id: str
    key: str
    version: str
    status: str
    spec_hash: str
    lint: list[str] = Field(default_factory=list)


class MeasurementOut(BaseModel):
    program_key: str
    treatment: int
    control: int
    holdout_pct: float
    treatment_rate: float
    control_rate: float
    lift_pp: float
    minimum_detectable_effect_pp: float
    significant: bool


class HealthOut(BaseModel):
    status: str
    database: bool
    migrations: list[str]
    isolation_enforced: bool
    connectors: list[str]


class SignupIn(BaseModel):
    """Redeeming an invitation.

    The company name and blueprint may be corrected here: the operator minted
    the invitation from what they were told, and the person signing up knows
    better than the person who typed it.
    """
    token: str = Field(min_length=8, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    blueprint_id: str | None = Field(default=None, max_length=80)


class KeyIn(BaseModel):
    """A new API key. Scopes narrow it; an empty list is full tenant access."""
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = Field(default=None, max_length=20)
