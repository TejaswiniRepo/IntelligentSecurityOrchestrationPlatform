"""Compare the LLM planner's first-turn choices with the deterministic planner on fixed scenarios.

Offline: uses the local Ollama model and the deterministic mock providers. No API costs.

    .venv\\Scripts\\python scripts\\eval_planner.py [--model qwen2.5:3b] [--runs 1]

Prints, per scenario, the actions each planner proposed, agreement, and LLM latency; then totals.
Useful for the dissertation evaluation chapter (agreement with baseline, latency, extra/missed calls).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time

from isop import providers
from isop.config import Settings
from isop.llm import OllamaProvider
from isop.planner import DeterministicPlanner, LlmPlanner, PlanningContext
from isop.policy import PolicyConfig, PolicyEngine
from isop.schemas import PlannedStep, Signal, SignalStatus

SCENARIOS: dict[str, dict[str, str | None]] = {
    "clean": {"email": "alice.walker@gmail.com", "phone": "+14155550123", "country": "US",
              "ip": "192.0.2.10", "device": "dev-a1b2c3"},
    "tor_ip": {"email": "bob.t@gmail.com", "phone": "+14155550188", "country": "US",
               "ip": "203.0.113.7", "device": "dev-x9y8z7"},
    "hosting_ip": {"email": "alice.walker@gmail.com", "phone": "+14155550123", "country": "US",
                   "ip": "198.51.100.20", "device": "dev-a1b2c3"},
    "conflicting": {"email": "dana.k@outlook.com", "phone": "+15555550123", "country": "GB",
                    "ip": "198.51.100.20", "device": "dev-q1w2e3"},
    "device_farm": {"email": "carol88@outlook.com", "phone": "+14155550199", "country": "US",
                    "ip": "198.51.100.20", "device": "DEV-789"},
    "shared_device": {"email": "alice.walker@gmail.com", "phone": "+14155550123", "country": "US",
                      "ip": "192.0.2.10", "device": "dev-shared-family"},
    "emulator_disposable": {"email": "x@mailinator.com", "phone": None, "country": "US",
                            "ip": "192.0.2.10", "device": "emu-pixel-01"},
    "disposable_only": {"email": "bob.smith@mailinator.com", "phone": "+14155550123", "country": "US",
                        "ip": "192.0.2.10", "device": "dev-a1b2c3"},
}


def baseline(case: dict[str, str | None]) -> list[Signal]:
    results = [("check_email_reputation", providers.email_reputation(case["email"])),
               ("check_ip_risk", providers.ip_risk(case["ip"])),
               ("check_device_risk", providers.device_risk(case["device"]))]
    if case["phone"]:
        results.insert(1, ("check_phone_risk", providers.phone_risk(case["phone"], case["country"])))
    return [Signal(signal_type=r.signal_type, tool=t, status=SignalStatus.SUCCESS, risk_score=r.risk_score,
                   reason_codes=r.reason_codes, source=r.source, simulated=True, details=r.details, latency_ms=0)
            for t, r in results]


def to_signal(tool: str, r) -> Signal:
    return Signal(signal_type=r.signal_type, tool=tool, status=SignalStatus.SUCCESS, risk_score=r.risk_score,
                  reason_codes=r.reason_codes, source=r.source, simulated=True, details=r.details, latency_ms=0)


def run_follow_ups(case: dict[str, str | None], steps: list[PlannedStep]) -> list[Signal]:
    out = []
    for s in steps:
        if s.tool == "check_device_history":
            out.append(to_signal(s.tool, providers.device_history(str(case["device"]))))
        elif s.tool == "get_registration_history" and s.entity in ("ip", "phone", "email") and case[s.entity]:
            out.append(to_signal(s.tool, providers.registration_history(s.entity, str(case[s.entity]))))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.getenv("OLLAMA_MODEL", Settings().ollama_model))
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()

    provider = OllamaProvider("http://127.0.0.1:11434", args.model, timeout_s=180)
    llm, rules = LlmPlanner(provider, fallback="error"), DeterministicPlanner()
    policy = PolicyEngine(PolicyConfig.load(Settings().policy_path))
    agree, decision_agree, latencies, extra, missed = 0, 0, [], 0, 0
    total = 0
    try:
        for name, case in SCENARIOS.items():
            evidence = baseline(case)
            ctx = PlanningContext(iteration=1, phone_provided=bool(case["phone"]), country=str(case["country"]),
                                  completed=frozenset(s.tool for s in evidence), remaining_budget=4)
            rule_steps = rules.propose(evidence, ctx)
            expected = {s.key for s in rule_steps}
            phone = bool(case["phone"])
            rule_decision = policy.evaluate(evidence + run_follow_ups(case, rule_steps), phone_provided=phone).decision
            for _ in range(args.runs):
                t0 = time.perf_counter()
                turn = await llm.plan(evidence, ctx)
                latencies.append(time.perf_counter() - t0)
                got = {s.key for s in turn.proposed}
                total += 1
                agree += got == expected
                extra += len(got - expected)
                missed += len(expected - got)
                llm_decision = policy.evaluate(evidence + run_follow_ups(case, turn.proposed),
                                               phone_provided=phone).decision
                decision_agree += llm_decision == rule_decision
                mark = "OK " if got == expected else "DIFF"
                print(f"[{mark}] {name:20} rules={sorted(expected)} -> {rule_decision}\n"
                      f"       {'':20} llm  ={sorted(got)} -> {llm_decision}  ({latencies[-1]:.1f}s)")
    finally:
        await provider.aclose()
    print(f"\nmodel={args.model} | tool-choice agreement {agree}/{total} | final-decision agreement "
          f"{decision_agree}/{total} | extra calls {extra} | missed calls {missed} | "
          f"latency median {statistics.median(latencies):.1f}s max {max(latencies):.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
