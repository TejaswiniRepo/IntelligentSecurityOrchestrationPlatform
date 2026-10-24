"""Investigation planners: choose follow-up tools from the evidence gathered so far.

A planner sees only normalized signals (type, status, score, reason codes) — never the raw
subject — and may only *name* tools. The orchestrator validates every step against an allowlist,
binds arguments itself from the validated request, and enforces the call budget. A planner can
therefore change how much evidence is collected, never what the policy decides.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from isop.schemas import PlannedStep, Signal, SignalStatus, SignalType

BASELINE_TOOLS = ("check_email_reputation", "check_phone_risk", "check_ip_risk", "check_device_risk")
FOLLOW_UP_TOOLS = frozenset({"check_device_history"})


class InvestigationPlanner(Protocol):
    name: str

    def plan_follow_ups(self, evidence: Sequence[Signal], completed_tools: frozenset[str]) -> list[PlannedStep]:
        """Return the next follow-up steps, or an empty list to stop investigating."""
        ...


class DeterministicPlanner:
    """Rule-based planner. Also the safe fallback for any future LLM planner."""

    name = "deterministic-v1"

    def __init__(self, device_follow_up_threshold: int = 40, conflict_high: int = 80, conflict_low: int = 20) -> None:
        self.device_follow_up_threshold = device_follow_up_threshold
        self.conflict_high = conflict_high
        self.conflict_low = conflict_low

    def plan_follow_ups(self, evidence: Sequence[Signal], completed_tools: frozenset[str]) -> list[PlannedStep]:
        if "check_device_history" in completed_tools:
            return []
        ok = [s for s in evidence if s.status is SignalStatus.SUCCESS and s.risk_score is not None]

        device = next((s for s in ok if s.signal_type is SignalType.DEVICE), None)
        if device is not None and device.risk_score >= self.device_follow_up_threshold:
            return [PlannedStep(
                tool="check_device_history",
                rationale=f"Device risk {device.risk_score} >= {self.device_follow_up_threshold} "
                          f"({', '.join(device.reason_codes)}); check linked accounts.",
            )]

        highs = [s for s in ok if s.risk_score >= self.conflict_high]
        lows = [s for s in ok if s.risk_score <= self.conflict_low]
        if highs and lows and device is not None:
            return [PlannedStep(
                tool="check_device_history",
                rationale=f"Conflicting evidence (high: {', '.join(s.signal_type for s in highs)}; "
                          f"low: {', '.join(s.signal_type for s in lows)}); corroborate with device history.",
            )]
        return []


def build_planner(kind: str) -> InvestigationPlanner:
    if kind == "deterministic":
        return DeterministicPlanner()
    raise ValueError(f"Unsupported ISOP_PLANNER={kind!r}; only 'deterministic' is implemented (see README).")
