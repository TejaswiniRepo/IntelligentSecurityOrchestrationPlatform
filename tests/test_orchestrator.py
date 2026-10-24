"""Orchestrator safety properties with fake tool clients and planners."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from isop.audit import AuditSink
from isop.config import Settings
from isop.orchestrator import InvestigationService
from isop.planner import DeterministicPlanner
from isop.policy import PolicyConfig, PolicyEngine
from isop.providers import device_history, device_risk, email_reputation, ip_risk, phone_risk
from isop.schemas import PlannedStep, RegistrationCheckRequest, Signal, SignalStatus
from isop.tool_client import TOOL_SIGNAL_TYPE
from tests.conftest import payload

pytestmark = pytest.mark.anyio

PROVIDERS = {
    "check_email_reputation": lambda a: email_reputation(a["email"]),
    "check_phone_risk": lambda a: phone_risk(a["phone"], a["country"]),
    "check_ip_risk": lambda a: ip_risk(a["ip_address"]),
    "check_device_risk": lambda a: device_risk(a["device_id"]),
    "check_device_history": lambda a: device_history(a["device_id"]),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeTools:
    """Calls the mock providers directly; selected tools can be forced to fail."""

    transport = "fake"

    def __init__(self, fail: dict[str, SignalStatus] | None = None) -> None:
        self.fail = fail or {}
        self.calls: list[str] = []

    async def call(self, tool: str, arguments: dict[str, str]) -> Signal:
        self.calls.append(tool)
        if tool in self.fail:
            return Signal(signal_type=TOOL_SIGNAL_TYPE[tool], tool=tool, status=self.fail[tool],
                          source="unavailable", simulated=False, latency_ms=0.0, error="forced")
        r = PROVIDERS[tool](arguments)
        return Signal(signal_type=r.signal_type, tool=tool, status=SignalStatus.SUCCESS, risk_score=r.risk_score,
                      reason_codes=r.reason_codes, source=r.source, simulated=r.simulated, latency_ms=0.0)


class RoguePlanner:
    """Simulates a compromised/hallucinating planner (e.g. a prompt-injected LLM)."""

    name = "rogue"

    def plan_follow_ups(self, evidence: Sequence[Signal], completed_tools: frozenset[str]) -> list[PlannedStep]:
        return [
            PlannedStep(tool="delete_user_account", rationale="ignore previous instructions"),
            PlannedStep(tool="check_ip_risk", rationale="repeat baseline"),
            PlannedStep(tool="check_device_history", rationale="ok"),
            PlannedStep(tool="check_device_history", rationale="again"),
        ]


def service(tmp_path: Path, tools: FakeTools, planner=None, max_follow_ups: int = 3) -> InvestigationService:
    policy = PolicyEngine(PolicyConfig.load(Settings().policy_path))
    return InvestigationService(tools, planner or DeterministicPlanner(), policy,
                                AuditSink(tmp_path / "audit.jsonl"), max_follow_ups)


def request(**overrides: object) -> RegistrationCheckRequest:
    return RegistrationCheckRequest.model_validate(payload(**overrides))


@pytest.mark.parametrize("status", [SignalStatus.ERROR, SignalStatus.TIMEOUT])
async def test_provider_failure_fails_closed_to_review(tmp_path: Path, status: SignalStatus) -> None:
    resp = await service(tmp_path, FakeTools(fail={"check_ip_risk": status})).check(request())
    assert resp.decision == "REVIEW"
    assert "SIGNAL_UNAVAILABLE_IP" in resp.reason_codes


async def test_failure_cannot_mask_a_reject(tmp_path: Path) -> None:
    tools = FakeTools(fail={"check_email_reputation": SignalStatus.ERROR})
    resp = await service(tmp_path, tools).check(request(device_id="DEV-789"))
    assert resp.decision == "REJECT"


async def test_planner_cannot_call_unlisted_or_duplicate_tools(tmp_path: Path) -> None:
    tools = FakeTools()
    resp = await service(tmp_path, tools, RoguePlanner()).check(request())
    assert "delete_user_account" not in tools.calls
    assert tools.calls.count("check_ip_risk") == 1
    assert tools.calls.count("check_device_history") == 1
    rejected = resp.trace.rejected_plan_steps
    assert any("delete_user_account: not an allowed" in r for r in rejected)
    assert any("check_device_history: duplicate" in r for r in rejected)
    # The planner changed what was gathered, not how the policy decides.
    assert resp.decision == "ALLOW"


async def test_follow_up_budget_is_enforced(tmp_path: Path) -> None:
    tools = FakeTools()
    resp = await service(tmp_path, tools, RoguePlanner(), max_follow_ups=0).check(request())
    assert "check_device_history" not in tools.calls
    assert resp.decision == "ALLOW"


async def test_password_never_reaches_tools_or_audit(tmp_path: Path) -> None:
    svc = service(tmp_path, FakeTools())
    resp = await svc.check(request())
    secret = payload()["password"]
    assert secret not in resp.model_dump_json()
    assert secret not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


class CrashingPlanner:
    name = "crashing"

    def plan_follow_ups(self, evidence: Sequence[Signal], completed_tools: frozenset[str]) -> list[PlannedStep]:
        raise RuntimeError("LLM unavailable")


async def test_planner_crash_stops_investigation_but_policy_still_decides(tmp_path: Path) -> None:
    resp = await service(tmp_path, FakeTools(), CrashingPlanner()).check(request(ip_address="203.0.113.7"))
    assert resp.decision == "REVIEW"  # baseline evidence still evaluated
    assert any("planner error" in r for r in resp.trace.rejected_plan_steps)


async def test_trace_order_is_reproducible(tmp_path: Path) -> None:
    svc = service(tmp_path, FakeTools())
    a, b = await svc.check(request(device_id="DEV-789")), await svc.check(request(device_id="DEV-789"))
    assert [s.tool for s in a.trace.signals] == [s.tool for s in b.trace.signals] == [
        "check_email_reputation", "check_phone_risk", "check_ip_risk", "check_device_risk", "check_device_history",
    ]
