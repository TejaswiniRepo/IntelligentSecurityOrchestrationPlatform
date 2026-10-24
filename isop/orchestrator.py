"""Coordinates one registration investigation: baseline -> planner follow-ups -> policy -> audit."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from isop.audit import AuditSink, mask_arguments, mask_email, mask_ip, mask_phone, pseudonymize
from isop.planner import BASELINE_TOOLS, FOLLOW_UP_TOOLS, InvestigationPlanner
from isop.policy import PolicyEngine
from isop.schemas import (
    AuditTrace,
    InvestigationSubject,
    PlannedStep,
    RegistrationCheckRequest,
    RegistrationCheckResponse,
    Signal,
    ToolInvocation,
)
from isop.tool_client import ToolClient

log = logging.getLogger("isop.orchestrator")

# Arguments are bound here from the validated subject — never supplied by the planner.
ARGUMENT_BINDINGS: dict[str, Callable[[InvestigationSubject], dict[str, str]]] = {
    "check_email_reputation": lambda s: {"email": s.email},
    "check_phone_risk": lambda s: {"phone": s.phone or "", "country": s.country},
    "check_ip_risk": lambda s: {"ip_address": s.ip_address},
    "check_device_risk": lambda s: {"device_id": s.device_id},
    "check_device_history": lambda s: {"device_id": s.device_id},
}


class InvestigationService:
    def __init__(
        self, tools: ToolClient, planner: InvestigationPlanner, policy: PolicyEngine, audit: AuditSink,
        max_follow_ups: int,
    ) -> None:
        self.tools = tools
        self.planner = planner
        self.policy = policy
        self.audit = audit
        self.max_follow_ups = max_follow_ups

    async def check(self, request: RegistrationCheckRequest) -> RegistrationCheckResponse:
        started = time.perf_counter()
        correlation_id = str(uuid.uuid4())
        subject = InvestigationSubject.from_request(request)  # password is dropped here
        signals: list[Signal] = []
        invocations: list[ToolInvocation] = []
        rejected: list[str] = []

        async def run(tool: str, phase: str, rationale: str) -> tuple[Signal, ToolInvocation]:
            args = ARGUMENT_BINDINGS[tool](subject)
            signal = await self.tools.call(tool, args)
            return signal, ToolInvocation(
                tool=tool, phase=phase, rationale=rationale, arguments_masked=mask_arguments(args),
                status=signal.status, latency_ms=signal.latency_ms,
            )

        async def run_all(calls: list[tuple[str, str, str]]) -> None:
            # Concurrent execution; results recorded in request order so traces are reproducible.
            for signal, invocation in await asyncio.gather(*(run(*c) for c in calls)):
                signals.append(signal)
                invocations.append(invocation)

        # 1. Baseline: independent checks run concurrently. Phone only when supplied.
        baseline = [t for t in BASELINE_TOOLS if t != "check_phone_risk" or subject.phone]
        await run_all([(t, "baseline", "baseline check") for t in baseline])

        # 2. Planner-driven follow-ups, bounded by allowlist, dedup and budget.
        completed = {i.tool for i in invocations}
        follow_ups = 0
        while follow_ups < self.max_follow_ups:
            try:
                steps = self.planner.plan_follow_ups(list(signals), frozenset(completed))
            except Exception as exc:  # planner failure ends investigation; policy still decides
                log.warning("planner=%s failed: %s", self.planner.name, type(exc).__name__)
                rejected.append(f"planner error: {type(exc).__name__}; investigation stopped")
                break
            accepted: list[PlannedStep] = []
            for step in steps:
                if step.tool not in FOLLOW_UP_TOOLS:
                    rejected.append(f"{step.tool}: not an allowed follow-up tool")
                elif step.tool in completed or any(a.tool == step.tool for a in accepted):
                    rejected.append(f"{step.tool}: duplicate call")
                elif follow_ups + len(accepted) >= self.max_follow_ups:
                    rejected.append(f"{step.tool}: follow-up budget exhausted")
                else:
                    accepted.append(step)
            if not accepted:
                break
            await run_all([(s.tool, "follow_up", s.rationale) for s in accepted])
            completed |= {s.tool for s in accepted}
            follow_ups += len(accepted)

        # 3. Deterministic policy decides.
        result = self.policy.evaluate(signals, phone_provided=subject.phone is not None)

        masked = {"email": mask_email(subject.email), "ip_address": mask_ip(subject.ip_address),
                  "device_id": pseudonymize(subject.device_id), "country": subject.country}
        if subject.phone:
            masked["phone"] = mask_phone(subject.phone)
        trace = AuditTrace(
            correlation_id=correlation_id,
            timestamp=datetime.now(UTC).isoformat(),
            planner=self.planner.name,
            tool_transport=f"mcp/{self.tools.transport}",
            subject_masked=masked,
            tools_invoked=invocations,
            rejected_plan_steps=rejected,
            signals=signals,
            policy=result,
            decision=result.decision,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        await asyncio.to_thread(self.audit.record, trace)
        log.info(
            "decision correlation_id=%s decision=%s composite=%s rules=%s tools=%s",
            correlation_id, result.decision, result.composite_score,
            [h.rule_id for h in result.rules_fired], [i.tool for i in invocations],
        )
        return RegistrationCheckResponse(
            correlation_id=correlation_id,
            decision=result.decision,
            reason_codes=result.reason_codes,
            composite_score=result.composite_score,
            policy_version=result.policy_version,
            simulated_signals=any(s.simulated for s in signals),
            trace=trace,
        )
