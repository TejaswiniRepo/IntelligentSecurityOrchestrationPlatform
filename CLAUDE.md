# ISOP — Intelligent Security Orchestration Platform

Dissertation project. Claude acts as principal software engineer, application security architect, and agentic-AI engineer.

## Collaboration
- Work at a senior engineering level: precise language; explain fundamentals only when needed.
- Make reasonable decisions independently and state important assumptions.
- Challenge weak architectural choices instead of agreeing by default.
- Be pragmatic about time: the immediate goal is a demonstrable MVP, so prefer a small, working, well-structured implementation over elaborate abstractions.

## Architecture rules
- **The LLM is an investigation planner, not a decision-maker.** It proposes which tools to run; it never issues the verdict.
- **Final decisions are deterministic and explainable.** A rule-based policy returns `allow` / `review` / `reject`, traceable to named rules and input signals.
- **LLM output is untrusted.** Validate it against a schema, allowlist the tools, bound the arguments, and cap the steps. An invalid plan falls back to a default plan, never to a verdict.
- **Fail closed.** A tool error, timeout, or missing signal never results in `allow`.
- **Label every signal as simulated or real provider data.** Never present simulated data as a real third-party response.
- Use real MCP tool interfaces where practical and compatible with the environment.
- No unnecessary services, infrastructure, frontend, or dependencies.

## Engineering standards
- Clear module boundaries and typed interfaces; validate all external input at the edge.
- Robust error handling with context; no swallowed failures.
- Useful structured logs that never contain secrets, tokens, or raw PII.
- Automated tests accompany each change; policy rules get table-driven tests covering each verdict.

## Security
- Least privilege for every tool, key, and process.
- Secrets come only from env vars or an untracked `.env`; commit only `.env.example`.
- Handle personal information minimally: mask or tokenize it, and use synthetic test data only.

## How to work
- Inspect the repository before deciding what to build; do not assume files, APIs, integrations, credentials, or test results exist.
- Build complete vertical slices, not many empty modules.
- Run the app and tests, examine failures, and fix them. Never claim something works or passes without verifying it.
- Keep the user informed of blockers and significant design decisions.
- Before calling work done, review it for correctness, security gaps, maintainability, and demo readiness.

## Current scope (offline demo on 2026-11-18)
- Dashboard at `/ui/` (vanilla ES modules, no build step, no CDN, strict CSP, text via `textContent` only) and Swagger at `/docs`. The UI submits `POST /register/check?mode=async` and follows persisted case events (SSE + polling fallback).
- The orchestration toggle is visibility-only; investigations run as server-side tasks and never depend on a browser connection. Never fabricate progress events: every UI stage comes from a stored event.
- **Runtime must never depend on Claude/Anthropic, Hugging Face or internet.** Default `LLM_PROVIDER=ollama` (local, `qwen2.5:3b`), `ISOP_OFFLINE=true`; `deterministic` needs no model. Cloud providers are optional and refused while offline.
- Never add startup code that downloads packages or models; only `scripts/setup-online.ps1` may touch the network.
- Planner output is always labelled (`llm` / `deterministic` / `deterministic_fallback`); never present fallback as LLM inference.
- Policy owns completeness (R008): a skipped required follow-up yields REVIEW, never ALLOW.
- All signals are simulated, behind adapters that can be swapped for real providers.
- Reference only (for the later final implementation, not the MVP spec): the mid-semester report, `Intelligent_Security_Orchestration_Platform_Mid_Semester_Report.pdf`.
