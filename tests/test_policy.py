"""Table-driven tests for the deterministic policy engine (no MCP, no randomness)."""

from __future__ import annotations

import pytest

from isop.config import Settings
from isop.policy import PolicyConfig, PolicyEngine
from isop.schemas import Decision, Signal, SignalStatus, SignalType

ENGINE = PolicyEngine(PolicyConfig.load(Settings().policy_path))


def sig(t: SignalType, score: int | None, *codes: str, status: SignalStatus = SignalStatus.SUCCESS) -> Signal:
    return Signal(signal_type=t, tool=f"tool_{t.value.lower()}", status=status, risk_score=score,
                  reason_codes=list(codes), source="test", simulated=True, latency_ms=0.0)


CLEAN = [sig(SignalType.EMAIL, 10), sig(SignalType.IP, 15), sig(SignalType.DEVICE, 15)]


def replace(signals: list[Signal], new: Signal) -> list[Signal]:
    return [s for s in signals if s.signal_type is not new.signal_type] + [new]


CASES = [
    ("all clean", CLEAN, False, Decision.ALLOW, {"R000_NO_RISK_INDICATORS"}),
    ("single high", replace(CLEAN, sig(SignalType.IP, 95)), False, Decision.REVIEW, {"R004_SINGLE_HIGH_RISK_SIGNAL"}),
    ("elevated", replace(CLEAN, sig(SignalType.IP, 65)), False, Decision.REVIEW, {"R005_ELEVATED_RISK_SIGNAL"}),
    ("two highs", replace(replace(CLEAN, sig(SignalType.IP, 95)), sig(SignalType.EMAIL, 90)), False,
     Decision.REJECT, {"R003_MULTIPLE_HIGH_RISK_SIGNALS", "R007_COMPOSITE_RISK_ELEVATED"}),
    ("hard reject code", CLEAN + [sig(SignalType.DEVICE_HISTORY, 40, "DEVICE_LINKED_TO_MANY_ACCOUNTS")], False,
     Decision.REJECT, {"R002_HARD_REJECT_INDICATOR"}),
    ("required signal errored", replace(CLEAN, sig(SignalType.IP, None, status=SignalStatus.ERROR)), False,
     Decision.REVIEW, {"R001_REQUIRED_SIGNAL_UNAVAILABLE"}),
    ("required signal timed out", replace(CLEAN, sig(SignalType.DEVICE, None, status=SignalStatus.TIMEOUT)), False,
     Decision.REVIEW, {"R001_REQUIRED_SIGNAL_UNAVAILABLE"}),
    ("phone supplied but missing", CLEAN, True, Decision.REVIEW, {"R001_REQUIRED_SIGNAL_UNAVAILABLE"}),
    ("no signals at all", [], False, Decision.REVIEW, {"R001_REQUIRED_SIGNAL_UNAVAILABLE"}),
    ("composite critical", [sig(SignalType.EMAIL, 75), sig(SignalType.IP, 75), sig(SignalType.DEVICE, 75)], False,
     Decision.REJECT, {"R005_ELEVATED_RISK_SIGNAL", "R006_COMPOSITE_RISK_CRITICAL"}),
    ("composite elevated", [sig(SignalType.EMAIL, 55), sig(SignalType.IP, 55), sig(SignalType.DEVICE, 30)], False,
     Decision.REVIEW, {"R007_COMPOSITE_RISK_ELEVATED"}),
]


@pytest.mark.parametrize(("name", "signals", "phone", "decision", "expected_rules"), CASES, ids=[c[0] for c in CASES])
def test_policy_table(name: str, signals: list[Signal], phone: bool, decision: Decision, expected_rules: set[str]) -> None:
    result = ENGINE.evaluate(signals, phone_provided=phone)
    assert result.decision is decision
    assert {h.rule_id for h in result.rules_fired} == expected_rules
    assert result.reason_codes and result.policy_version == "reg-policy-2026.10.1"


def test_failed_signal_is_not_scored_as_low_risk() -> None:
    errored = replace(CLEAN, sig(SignalType.IP, None, status=SignalStatus.ERROR))
    result = ENGINE.evaluate(errored, phone_provided=False)
    assert result.decision is not Decision.ALLOW
    assert "SIGNAL_UNAVAILABLE_IP" in result.reason_codes


def test_thresholds_are_configurable() -> None:
    strict = ENGINE.config.model_copy(update={"elevated_risk_threshold": 10, "high_risk_threshold": 12})
    assert PolicyEngine(strict).evaluate(CLEAN, phone_provided=False).decision is Decision.REJECT


@pytest.mark.parametrize(
    "bad",
    [
        {"elevated_risk_threshold": 90},  # above high threshold
        {"composite_review_threshold": 80},  # above reject threshold
        {"weights": {"EMAIL": 1.0}},  # incomplete weights
    ],
)
def test_inconsistent_config_is_rejected(bad: dict) -> None:
    raw = ENGINE.config.model_dump(mode="json") | bad
    with pytest.raises(ValueError):
        PolicyConfig.model_validate(raw)
