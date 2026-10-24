"""Typed contracts shared across the API, tool layer, planner, policy and audit."""

from __future__ import annotations

import re
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")


class Decision(StrEnum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class SignalType(StrEnum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IP = "IP"
    DEVICE = "DEVICE"
    DEVICE_HISTORY = "DEVICE_HISTORY"


class SignalStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


# --------------------------------------------------------------------------- API


class RegistrationCheckRequest(BaseModel):
    """Registration details submitted by the registration service for a risk check."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "username": "alice_w",
                "email": "alice.walker@gmail.com",
                "password": "correct-horse-battery-staple",
                "phone": "+14155550123",
                "country": "US",
                "ip_address": "192.0.2.10",
                "device_id": "dev-a1b2c3",
            }
        },
    )

    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)
    phone: str | None = Field(default=None, description="E.164 format, e.g. +14155550123")
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    ip_address: IPv4Address | IPv6Address = Field(description="End-user IP observed by the registration service")
    device_id: str = Field(description="Device fingerprint reference")

    @field_validator("phone")
    @classmethod
    def _phone_e164(cls, v: str | None) -> str | None:
        if v is not None and not E164.fullmatch(v):
            raise ValueError("phone must be E.164, e.g. +14155550123")
        return v

    @field_validator("country")
    @classmethod
    def _country_upper(cls, v: str) -> str:
        if not v.isalpha():
            raise ValueError("country must be a 2-letter ISO code")
        return v.upper()

    @field_validator("device_id")
    @classmethod
    def _device_id(cls, v: str) -> str:
        if not DEVICE_ID.fullmatch(v):
            raise ValueError("device_id must be 3-64 chars of [A-Za-z0-9_-]")
        return v


class InvestigationSubject(BaseModel):
    """The minimum attributes the investigation needs. The password never enters this object."""

    model_config = ConfigDict(frozen=True)

    email: str
    phone: str | None
    country: str
    ip_address: str
    device_id: str

    @classmethod
    def from_request(cls, req: RegistrationCheckRequest) -> InvestigationSubject:
        return cls(
            email=str(req.email).lower(),
            phone=req.phone,
            country=req.country,
            ip_address=str(req.ip_address),
            device_id=req.device_id,
        )


# --------------------------------------------------------------------------- tools / evidence


class ProviderResult(BaseModel):
    """Structured output every MCP signal tool must return. Validated on the client side."""

    model_config = ConfigDict(extra="forbid")

    signal_type: SignalType
    risk_score: int = Field(ge=0, le=100)
    reason_codes: list[str]
    source: str
    simulated: bool
    details: dict[str, str | int | float | bool] = Field(default_factory=dict)


class Signal(BaseModel):
    """A normalized piece of evidence. `status` is independent of risk: a failed check is not low risk."""

    signal_type: SignalType
    tool: str
    status: SignalStatus
    risk_score: int | None = Field(default=None, ge=0, le=100)
    reason_codes: list[str] = Field(default_factory=list)
    source: str
    simulated: bool
    details: dict[str, str | int | float | bool] = Field(default_factory=dict)
    latency_ms: float
    error: str | None = None


class PlannedStep(BaseModel):
    """A planner's request to run a tool. Planners choose tools, never arguments."""

    tool: str
    rationale: str = Field(max_length=300)


# --------------------------------------------------------------------------- policy / audit / response


class RuleHit(BaseModel):
    rule_id: str
    outcome: Decision
    reason_codes: list[str]
    detail: str


class PolicyResult(BaseModel):
    decision: Decision
    policy_version: str
    composite_score: float
    rules_fired: list[RuleHit]
    reason_codes: list[str]


class ToolInvocation(BaseModel):
    tool: str
    phase: str  # "baseline" | "follow_up"
    rationale: str
    arguments_masked: dict[str, str]
    status: SignalStatus
    latency_ms: float


class AuditTrace(BaseModel):
    correlation_id: str
    timestamp: str
    planner: str
    tool_transport: str
    subject_masked: dict[str, str]
    tools_invoked: list[ToolInvocation]
    rejected_plan_steps: list[str]
    signals: list[Signal]
    policy: PolicyResult
    decision: Decision
    duration_ms: float


class RegistrationCheckResponse(BaseModel):
    correlation_id: str
    decision: Decision
    reason_codes: list[str]
    composite_score: float
    policy_version: str
    simulated_signals: bool = Field(description="True if any signal came from a simulated provider")
    trace: AuditTrace
