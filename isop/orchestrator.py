"""Coordinates one registration investigation: baseline -> planner loop -> policy -> audit."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from isop.audit import AuditSink, mask_arguments, mask_email, mask_ip, mask_phone, pseudonymize
from isop.planner import (
    BASELINE_TOOLS,
    FOLLOW_UP_TOOLS,
    InvestigationPlanner,
    PlannerUnavailableError,
    PlanningContext,
    remaining_actions,
)
from isop.policy import PolicyEngine
from isop.schemas import (
    AuditTrace,
    InvestigationSubject,
    InvestigationSummary,
    PlannedStep,
    PlannerTurn,
    RegistrationCheckRequest,
    RegistrationCheckResponse,
    Signal,
    SignalStatus,
    ToolInvocation,
)
from isop.tool_client import ToolClient

log = logging.getLogger("isop.orchestrator")

Emit = Callable[[str, str, str, dict[str, Any]], None]


def signal_view(s: Signal) -> dict[str, Any]:
    """Event-safe view of a signal: provider details are scalar, non-identifying values."""
    return {"signal_type": s.signal_type.value, "status": s.status.value, "risk_score": s.risk_score,
            "reason_codes": s.reason_codes, "details": s.details, "source": s.source, "simulated": s.simulated,
            "latency_ms": s.latency_ms, "error": s.error}

# Argument values are bound here from the validated subject — never supplied by a planner or LLM.
ARGUMENT_BINDINGS: dict[str, Callable[[InvestigationSubject, str | None], dict[str, str]]] = {
    "check_email_reputation": lambda s, _: {"email": s.email},
    "check_phone_risk": lambda s, _: {"phone": s.phone or "", "country": s.country or ""},
    "check_ip_risk": lambda s, _: {"ip_address": s.ip_address},
    "check_device_risk": lambda s, _: {"device_id": s.device_id},
    "check_device_history": lambda s, _: {"device_id": s.device_id},
    "get_registration_history": lambda s, e: {
        "entity_type": e or "", "entity_value": {"ip": s.ip_address, "phone": s.phone or "", "email": s.email}[e or "ip"],
    },
}

__all__ = ["InvestigationService", "PlannerUnavailableError"]


def summarize(turns: list[PlannerTurn], requested: str, model: str | None) -> InvestigationSummary:
    modes = {t.mode for t in turns}
    effective = "none" if not modes else (modes.pop() if len(modes) == 1 else "mixed")
    return InvestigationSummary(
        requested_provider=requested, model=model, effective_mode=effective,
        fallback_used=any(t.mode == "deterministic_fallback" for t in turns),
        llm_calls=sum(1 for t in turns if t.provider != "deterministic" and t.status != "skipped"),
        turns=turns,
    )


class InvestigationService:
    def __init__(
        self, tools: ToolClient, planner: InvestigationPlanner, policy: PolicyEngine, audit: AuditSink,
        max_follow_ups: int, max_iterations: int = 3, requested_provider: str = "deterministic",
        model: str | None = None,
    ) -> None:
        self.tools = tools
        self.planner = planner
        self.policy = policy
        self.audit = audit
        self.max_follow_ups = max_follow_ups
        self.max_iterations = max_iterations
        self.requested_provider = requested_provider
        self.model = model

    def _validate(self, step: PlannedStep, subject: InvestigationSubject, completed: set[str],
                  accepted: list[PlannedStep], used: int) -> str | None:
        """Return a rejection reason, or None if the step may run."""
        if step.tool not in FOLLOW_UP_TOOLS:
            return "not an allowed follow-up tool"
        if step.entity not in FOLLOW_UP_TOOLS[step.tool]:
            return f"entity {step.entity!r} not allowed for this tool"
        if step.entity == "phone" and not subject.phone:
            return "no phone number was supplied"
        if step.key in completed or any(a.key == step.key for a in accepted):
            return "duplicate call"
        if used + len(accepted) >= self.max_follow_ups:
            return "follow-up budget exhausted"
        return None

    async def check(self, request: RegistrationCheckRequest, *, case_id: str | None = None,
                    emit: Emit | None = None) -> RegistrationCheckResponse:
        """Run one investigation. `emit(type, stage, status, details)` receives sanitised progress events."""
        started = time.perf_counter()
        case_id = case_id or str(uuid.uuid4())
        subject = InvestigationSubject.from_request(request)  # password and names are dropped here
        signals: list[Signal] = []
        invocations: list[ToolInvocation] = []
        rejected: list[str] = []
        turns: list[PlannerTurn] = []

        def ev(type_: str, stage: str, status: str, /, **details: Any) -> None:
            if emit is not None:
                emit(type_, stage, status, details)

        masked = {"email": mask_email(subject.email), "ip_address": mask_ip(subject.ip_address),
                  "device_id": pseudonymize(subject.device_id), "country": subject.country or "not supplied"}
        if subject.phone:
            masked["phone"] = mask_phone(subject.phone)
        if subject.device_fingerprint:
            masked["device_fingerprint"] = pseudonymize(subject.device_fingerprint)
        ev("context.prepared", "context", "completed", subject_masked=masked, phone_provided=bool(subject.phone),
           planner_provider=self.requested_provider, planner_model=self.model,
           note="Identifiers masked for audit; the planner receives risk signals only, never raw identifiers.")

        async def run(tool: str, entity: str | None, phase: str, by: str, why: str) -> tuple[Signal, ToolInvocation]:
            args = ARGUMENT_BINDINGS[tool](subject, entity)
            ev("tool.started", phase, "running", tool=tool, entity=entity, requested_by=by, rationale=why,
               arguments_masked=mask_arguments(args))
            signal = await self.tools.call(tool, args)
            ev("tool.completed", phase, "completed" if signal.status is SignalStatus.SUCCESS else "failed",
               tool=tool, entity=entity, requested_by=by, signal=signal_view(signal))
            return signal, ToolInvocation(
                tool=tool, entity=entity, phase=phase, requested_by=by, rationale=why,
                arguments_masked=mask_arguments(args), status=signal.status, latency_ms=signal.latency_ms,
            )

        async def run_all(calls: list[tuple[str, str | None, str, str, str]]) -> None:
            # Concurrent execution; results recorded in request order so traces are reproducible.
            for signal, invocation in await asyncio.gather(*(run(*c) for c in calls)):
                signals.append(signal)
                invocations.append(invocation)

        # 1. Baseline checks are mandated by policy, not chosen by the planner. Phone only when supplied.
        baseline = [t for t in BASELINE_TOOLS if t != "check_phone_risk" or subject.phone]
        ev("baseline.started", "baseline", "running", tools=baseline,
           skipped=[] if subject.phone else ["check_phone_risk (no phone supplied)"])
        await run_all([(t, None, "baseline", "policy-baseline", "mandatory baseline check") for t in baseline])
        ev("baseline.completed", "baseline", "completed",
           succeeded=sum(s.status is SignalStatus.SUCCESS for s in signals),
           failed=sum(s.status is not SignalStatus.SUCCESS for s in signals))

        # 2. Planner loop: the planner sees results and may ask for more, within limits.
        completed: set[str] = {i.tool for i in invocations}
        used = 0
        stop_reason = "max_iterations"
        for iteration in range(1, self.max_iterations + 1):
            if used >= self.max_follow_ups:
                stop_reason = "budget_exhausted"
                break
            ctx = PlanningContext(iteration=iteration, phone_provided=subject.phone is not None,
                                  country=subject.country, completed=frozenset(completed),
                                  remaining_budget=self.max_follow_ups - used)
            available = remaining_actions(ctx)
            if not available:
                stop_reason = "no_remaining_actions"  # every follow-up has run; no point asking again
                break
            ev("planner.started", "planner", "running", iteration=iteration, provider=self.requested_provider,
               model=self.model, available_actions=available, evidence_signals=len(signals))
            try:
                turn = await self.planner.plan(list(signals), ctx)
            except PlannerUnavailableError as exc:
                ev("planner.completed", "planner", "failed", iteration=iteration, error=str(exc))
                raise
            except Exception as exc:  # planner bug: stop investigating; policy still decides on evidence
                log.warning("planner=%s crashed: %s", self.planner.name, type(exc).__name__)
                rejected.append(f"planner error: {type(exc).__name__}; investigation stopped")
                ev("planner.completed", "planner", "failed", iteration=iteration,
                   error=f"planner error ({type(exc).__name__}); investigation stopped")
                stop_reason = "planner_error"
                break
            turns.append(turn)
            # A fallback turn still planned successfully; its `mode`/`error` say the LLM was not used.
            turn_view = turn.model_dump(mode="json")
            turn_view["llm_status"] = turn_view.pop("status")  # ok | error | skipped (the LLM call itself)
            ev("planner.completed", "planner", "completed", **turn_view)

            accepted: list[PlannedStep] = []
            for step in turn.proposed:
                reason = self._validate(step, subject, completed, accepted, used)
                if reason:
                    rejected.append(f"{step.key}: {reason}")
                    ev("plan.step_rejected", "planner", "skipped", step=step.key, reason=reason)
                else:
                    accepted.append(step)
            if not accepted:
                stop_reason = "planner_done" if not turn.proposed else "no_valid_steps"
                break
            await run_all([(s.tool, s.entity, "follow_up", turn.mode, s.rationale) for s in accepted])
            completed |= {s.key for s in accepted}
            used += len(accepted)
            if turn.done:
                stop_reason = "planner_done"
                break
        ev("planner.concluded", "planner", "completed", stop_reason=stop_reason, turns=len(turns),
           follow_ups_run=used)
        if used == 0:
            ev("follow_up.skipped", "follow_up", "skipped", reason=stop_reason)

        # 3. Aggregate the evidence, then the deterministic policy decides.
        ok = [s for s in signals if s.status is SignalStatus.SUCCESS and s.risk_score is not None]
        top = max(ok, key=lambda s: s.risk_score or 0, default=None)
        ev("aggregation.completed", "aggregation", "completed",
           signals=[{"signal_type": s.signal_type.value, "tool": s.tool, "status": s.status.value,
                     "risk_score": s.risk_score} for s in signals],
           succeeded=len(ok), failed=len(signals) - len(ok),
           highest={"signal_type": top.signal_type.value, "risk_score": top.risk_score} if top else None)
        ev("policy.started", "policy", "running", policy_version=self.policy.config.version)
        result = self.policy.evaluate(signals, phone_provided=subject.phone is not None)
        ev("policy.completed", "policy", "completed", decision=result.decision.value,
           composite_score=result.composite_score, policy_version=result.policy_version,
           reason_codes=result.reason_codes, rules_fired=[h.model_dump(mode="json") for h in result.rules_fired])
        investigation = summarize(turns, self.requested_provider, self.model)
        trace = AuditTrace(
            correlation_id=case_id,
            timestamp=datetime.now(UTC).isoformat(),
            investigation=investigation,
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
        ev("audit.recorded", "decision", "completed", store="SQLite", masked=True)
        log.info(
            "decision case_id=%s decision=%s composite=%s rules=%s planner_mode=%s tools=%s",
            case_id, result.decision, result.composite_score, [h.rule_id for h in result.rules_fired],
            investigation.effective_mode, [i.tool + (f":{i.entity}" if i.entity else "") for i in invocations],
        )
        return RegistrationCheckResponse(
            correlation_id=case_id,
            decision=result.decision,
            reason_codes=result.reason_codes,
            composite_score=result.composite_score,
            policy_version=result.policy_version,
            simulated_signals=any(s.simulated for s in signals),
            planner_mode=investigation.effective_mode,
            trace=trace,
        )
