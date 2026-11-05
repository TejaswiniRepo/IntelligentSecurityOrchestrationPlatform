"""Orchestrator safety properties with fake tools/planners and deliberately broken MCP servers."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from mcp.server import MCPServer

from isop import providers
from isop.audit import AuditSink
from isop.config import Settings
from isop.orchestrator import InvestigationService
from isop.planner import DeterministicPlanner, PlanningContext
from isop.policy import PolicyConfig, PolicyEngine
from isop.schemas import PlannedStep, PlannerTurn, RegistrationCheckRequest, Signal, SignalStatus
from isop.tool_client import TOOL_SIGNAL_TYPE, McpToolClient
from tests.conftest import payload

pytestmark = pytest.mark.anyio

PROVIDERS = {
    "check_email_reputation": lambda a: providers.email_reputation(a["email"]),
    "check_phone_risk": lambda a: providers.phone_risk(a["phone"], a["country"]),
    "check_ip_risk": lambda a: providers.ip_risk(a["ip_address"]),
    "check_device_risk": lambda a: providers.device_risk(a["device_id"]),
    "check_device_history": lambda a: providers.device_history(a["device_id"]),
    "get_registration_history": lambda a: providers.registration_history(a["entity_type"], a["entity_value"]),
}


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
                      reason_codes=r.reason_codes, source=r.source, simulated=r.simulated, details=r.details,
                      latency_ms=0.0)


class RoguePlanner:
    """Simulates a compromised or hallucinating planner (e.g. a prompt-injected LLM)."""

    name = "rogue"

    async def plan(self, evidence: Sequence[Signal], ctx: PlanningContext) -> PlannerTurn:
        return PlannerTurn(iteration=ctx.iteration, mode="llm", provider="ollama", status="ok", proposed=[
            PlannedStep(tool="delete_user_account", rationale="ignore previous instructions"),
            PlannedStep(tool="check_ip_risk", rationale="repeat baseline"),
            PlannedStep(tool="get_registration_history", entity="device", rationale="wrong entity"),
            PlannedStep(tool="check_device_history", rationale="ok"),
            PlannedStep(tool="check_device_history", rationale="again"),
        ])


class CrashingPlanner:
    name = "crashing"

    async def plan(self, evidence: Sequence[Signal], ctx: PlanningContext) -> PlannerTurn:
        raise RuntimeError("bug")


def service(tmp_path: Path, tools, planner=None, max_follow_ups: int = 4) -> InvestigationService:
    policy = PolicyEngine(PolicyConfig.load(Settings().policy_path))
    return InvestigationService(tools, planner or DeterministicPlanner(), policy,
                                AuditSink(tmp_path / "audit.db"), max_follow_ups)


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


async def test_failed_follow_up_leaves_review_not_allow(tmp_path: Path) -> None:
    tools = FakeTools(fail={"check_device_history": SignalStatus.ERROR})
    resp = await service(tmp_path, tools).check(request(device_id="dev-shared-family"))
    assert resp.decision == "REVIEW"
    assert "FOLLOW_UP_MISSING_DEVICE_HISTORY" in resp.reason_codes


async def test_planner_cannot_call_unlisted_wrong_entity_or_duplicate_tools(tmp_path: Path) -> None:
    tools = FakeTools()
    resp = await service(tmp_path, tools, RoguePlanner()).check(request())
    assert "delete_user_account" not in tools.calls
    assert tools.calls.count("check_ip_risk") == 1
    assert tools.calls.count("check_device_history") == 1
    assert "get_registration_history" not in tools.calls
    rejected = resp.trace.rejected_plan_steps
    assert any("delete_user_account: not an allowed" in r for r in rejected)
    assert any("get_registration_history:device: entity" in r for r in rejected)
    assert any("check_device_history: duplicate" in r for r in rejected)
    # The planner changed what was gathered, not how the policy decides.
    assert resp.decision == "ALLOW"


async def test_follow_up_budget_is_enforced(tmp_path: Path) -> None:
    tools = FakeTools()
    resp = await service(tmp_path, tools, RoguePlanner(), max_follow_ups=0).check(request())
    assert "check_device_history" not in tools.calls
    assert resp.decision == "ALLOW"


async def test_planner_crash_stops_investigation_but_policy_still_decides(tmp_path: Path) -> None:
    resp = await service(tmp_path, FakeTools(), CrashingPlanner()).check(request(ip_address="203.0.113.7"))
    assert resp.decision == "REVIEW"
    assert any("planner error" in r for r in resp.trace.rejected_plan_steps)


async def test_password_never_reaches_tools_or_audit(tmp_path: Path) -> None:
    resp = await service(tmp_path, FakeTools()).check(request())
    secret = payload()["password"]
    assert secret not in resp.model_dump_json()
    assert secret.encode() not in (tmp_path / "audit.db").read_bytes()


async def test_trace_order_is_reproducible(tmp_path: Path) -> None:
    svc = service(tmp_path, FakeTools())
    a, b = await svc.check(request(device_id="DEV-789")), await svc.check(request(device_id="DEV-789"))
    assert [s.tool for s in a.trace.signals] == [s.tool for s in b.trace.signals] == [
        "check_email_reputation", "check_phone_risk", "check_ip_risk", "check_device_risk", "check_device_history",
    ]


# ---------------------------------------------------------------- real MCP client, broken MCP servers


def partial_server(*, slow_ip: bool = False, drop: str | None = None) -> MCPServer:
    from isop import mcp_server as real

    srv = MCPServer(name="isop-signals-broken")
    for name in ("check_email_reputation", "check_phone_risk", "check_device_risk", "check_device_history",
                 "get_registration_history"):
        if name != drop:
            srv.tool()(getattr(real, name))
    if drop != "check_ip_risk":
        if slow_ip:
            @srv.tool(name="check_ip_risk")
            async def slow(ip_address: str) -> dict:
                await asyncio.sleep(2)
                return providers.ip_risk(ip_address).model_dump(mode="json")
        else:
            srv.tool()(real.check_ip_risk)
    return srv


async def test_unavailable_mcp_tool_fails_closed(tmp_path: Path) -> None:
    async with McpToolClient("inprocess", 2.0, partial_server(drop="check_ip_risk")) as tools:
        resp = await service(tmp_path, tools).check(request())
    ip = next(s for s in resp.trace.signals if s.tool == "check_ip_risk")
    assert ip.status is SignalStatus.ERROR and ip.error == "tool not available on MCP server"
    assert resp.decision == "REVIEW"


async def test_slow_mcp_tool_times_out_and_fails_closed(tmp_path: Path) -> None:
    async with McpToolClient("inprocess", 0.3, partial_server(slow_ip=True)) as tools:
        resp = await service(tmp_path, tools).check(request())
    ip = next(s for s in resp.trace.signals if s.tool == "check_ip_risk")
    assert ip.status is SignalStatus.TIMEOUT
    assert resp.decision == "REVIEW"
