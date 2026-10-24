"""Deterministic, versioned registration policy. The only component that produces a decision.

Rules are evaluated independently; the final decision is the most severe outcome among the rules
that fired (REJECT > REVIEW > ALLOW). Thresholds live in config/policy.json.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from isop.schemas import Decision, PolicyResult, RuleHit, Signal, SignalStatus, SignalType

_SEVERITY = {Decision.ALLOW: 0, Decision.REVIEW: 1, Decision.REJECT: 2}


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    high_risk_threshold: int = Field(ge=0, le=100)
    elevated_risk_threshold: int = Field(ge=0, le=100)
    composite_review_threshold: float = Field(ge=0, le=100)
    composite_reject_threshold: float = Field(ge=0, le=100)
    high_risk_signals_to_reject: int = Field(ge=1)
    hard_reject_reason_codes: frozenset[str]
    required_signals: frozenset[SignalType]
    weights: dict[SignalType, float]

    @model_validator(mode="after")
    def _consistent(self) -> PolicyConfig:
        if self.elevated_risk_threshold >= self.high_risk_threshold:
            raise ValueError("elevated_risk_threshold must be below high_risk_threshold")
        if self.composite_review_threshold >= self.composite_reject_threshold:
            raise ValueError("composite_review_threshold must be below composite_reject_threshold")
        if any(w <= 0 for w in self.weights.values()) or set(self.weights) != set(SignalType):
            raise ValueError("weights must be positive and cover every SignalType")
        return self

    @classmethod
    def load(cls, path: Path) -> PolicyConfig:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def evaluate(self, signals: Sequence[Signal], *, phone_provided: bool) -> PolicyResult:
        c = self.config
        ok: dict[SignalType, Signal] = {}
        for s in signals:
            if s.status is SignalStatus.SUCCESS and s.risk_score is not None:
                prev = ok.get(s.signal_type)
                if prev is None or s.risk_score > (prev.risk_score or 0):
                    ok[s.signal_type] = s

        hits: list[RuleHit] = []

        # R001 fail closed: a required check that did not succeed is never treated as low risk.
        required = set(c.required_signals) | ({SignalType.PHONE} if phone_provided else set())
        missing = sorted(t.value for t in required if t not in ok)
        if missing:
            hits.append(RuleHit(
                rule_id="R001_REQUIRED_SIGNAL_UNAVAILABLE", outcome=Decision.REVIEW,
                reason_codes=[f"SIGNAL_UNAVAILABLE_{m}" for m in missing],
                detail=f"Required signals unavailable: {', '.join(missing)}",
            ))

        # R002 explicit hard-reject indicators.
        hard = sorted({rc for s in ok.values() for rc in s.reason_codes} & c.hard_reject_reason_codes)
        if hard:
            hits.append(RuleHit(
                rule_id="R002_HARD_REJECT_INDICATOR", outcome=Decision.REJECT, reason_codes=hard,
                detail="Configured hard-reject indicator present",
            ))

        # R003 / R004 high-risk signal count.
        high = sorted((s for s in ok.values() if s.risk_score >= c.high_risk_threshold), key=lambda s: s.signal_type)
        high_codes = [f"HIGH_RISK_{s.signal_type.value}" for s in high]
        if len(high) >= c.high_risk_signals_to_reject:
            hits.append(RuleHit(
                rule_id="R003_MULTIPLE_HIGH_RISK_SIGNALS", outcome=Decision.REJECT, reason_codes=high_codes,
                detail=f"{len(high)} signals >= {c.high_risk_threshold}",
            ))
        elif high:
            hits.append(RuleHit(
                rule_id="R004_SINGLE_HIGH_RISK_SIGNAL", outcome=Decision.REVIEW, reason_codes=high_codes,
                detail=f"One signal >= {c.high_risk_threshold}; insufficient alone to reject",
            ))

        # R005 elevated (but not high) signals.
        elevated = sorted(
            (s for s in ok.values() if c.elevated_risk_threshold <= s.risk_score < c.high_risk_threshold),
            key=lambda s: s.signal_type,
        )
        if elevated:
            hits.append(RuleHit(
                rule_id="R005_ELEVATED_RISK_SIGNAL", outcome=Decision.REVIEW,
                reason_codes=[f"ELEVATED_RISK_{s.signal_type.value}" for s in elevated],
                detail=f"Signals in [{c.elevated_risk_threshold}, {c.high_risk_threshold})",
            ))

        # R006 / R007 weighted composite across successful signals.
        total_w = sum(c.weights[t] for t in ok)
        composite = round(sum(c.weights[t] * s.risk_score for t, s in ok.items()) / total_w, 1) if total_w else 0.0
        if composite >= c.composite_reject_threshold:
            hits.append(RuleHit(
                rule_id="R006_COMPOSITE_RISK_CRITICAL", outcome=Decision.REJECT, reason_codes=["COMPOSITE_RISK_CRITICAL"],
                detail=f"Composite {composite} >= {c.composite_reject_threshold}",
            ))
        elif composite >= c.composite_review_threshold:
            hits.append(RuleHit(
                rule_id="R007_COMPOSITE_RISK_ELEVATED", outcome=Decision.REVIEW, reason_codes=["COMPOSITE_RISK_ELEVATED"],
                detail=f"Composite {composite} >= {c.composite_review_threshold}",
            ))

        if not hits:
            hits.append(RuleHit(
                rule_id="R000_NO_RISK_INDICATORS", outcome=Decision.ALLOW, reason_codes=["NO_RISK_INDICATORS"],
                detail="All required signals succeeded below thresholds",
            ))

        decision = max((h.outcome for h in hits), key=_SEVERITY.__getitem__)
        reason_codes = list(dict.fromkeys(rc for h in hits for rc in h.reason_codes))
        return PolicyResult(
            decision=decision, policy_version=c.version, composite_score=composite,
            rules_fired=hits, reason_codes=reason_codes,
        )
