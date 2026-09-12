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
    # What was counted, and for how long after each account entered. A rate
    # with no metric beside it is a rate of something the reader has to guess,
    # and every programme used to be measured on the same three outcome types
    # whatever it declared (D-51).
    primary_metric: str
    metric_counts: str
    metric_window_days: int


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


class BaselineIn(BaseModel):
    """Ninety days before Zolts, to be frozen once.

    Spend is EUR per month in micros, four fields (`docs/17`). The funnel is
    the window's: contacts made, replies, meetings, opportunities. `signed_by`
    names the person on the partner's side who signs the letter that quotes
    the digest.
    """
    window_start: str = Field(min_length=10, max_length=10)
    window_end: str = Field(min_length=10, max_length=10)
    spend_tools_micros: int = Field(ge=0)
    spend_data_micros: int = Field(ge=0)
    spend_sending_micros: int = Field(ge=0)
    spend_people_micros: int = Field(ge=0)
    contacted: int = Field(ge=0)
    replied: int = Field(ge=0)
    meetings: int = Field(ge=0)
    opportunities: int = Field(ge=0)
    source: str = Field(default="declared", pattern="^(declared|crm)$")
    signed_by: str = Field(min_length=1, max_length=200)


class BatchIn(BaseModel):
    """One act over many rows, with one reason for all of them (docs/28, OX-7).

    The reason is required in spirit and checked by the rule rather than the
    schema, so a thin one is refused with the same three names every other
    override uses instead of a validation error about a missing field.
    """
    act: str = Field(min_length=1, max_length=40)
    ids: list[str] = Field(default_factory=list, max_length=1000)
    reason: str | None = Field(default=None, max_length=500)


class SendingActIn(BaseModel):
    """Stopping a domain sending, or letting it send again.

    The reason is the whole body, and it is required rather than optional.
    `zolts.sendingcontrol` decides whether what was written is a reason: this
    only refuses what is plainly absent, so the operator gets the specific
    message about *why* their text was not accepted rather than a schema error
    that says a field is missing when they filled it in.
    """
    reason: str = Field(min_length=1, max_length=500)


class KeyIn(BaseModel):
    """A new API key. Scopes narrow it; an empty list is full tenant access."""
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = Field(default=None, max_length=20)


class SessionIn(BaseModel):
    """Opening a browser session with an API key."""
    api_key: str = Field(min_length=8, max_length=200)


class EnrichIn(BaseModel):
    """Buying a missing field for named entities.

    Named rather than "everything unresolved": this spends a data budget, and
    an endpoint whose cost depends on how much happened to be missing is one
    nobody can predict the bill for.
    """
    field: Literal["email", "phone", "firmographics"]
    ids: list[str] = Field(min_length=1, max_length=200)
    # Enrichment obtains personal data from a third party. The basis is
    # recorded against every value bought and cannot be reconstructed later.
    legal_basis: Literal["legitimate_interest", "consent", "contract"] = \
        "legitimate_interest"


class ResearchIn(BaseModel):
    """Asking for a research dossier on named accounts.

    Named rather than "the whole book": at 20 credits it is the most expensive
    action in the price list, and an endpoint that researches everything
    unresearched is one nobody can predict the bill for.

    `force` rewrites a dossier that is still current, and costs again. It
    exists because an operator who does not believe one should be able to say
    so, and it is off by default because the reason a dossier is reused is
    that nothing has changed.
    """
    account_ids: list[str] = Field(min_length=1, max_length=25)
    force: bool = False
