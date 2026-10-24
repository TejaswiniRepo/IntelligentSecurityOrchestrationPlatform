# ISOP — Intelligent Security Orchestration Platform (MVP)

Registration fraud pre-check. A registration service calls `POST /register/check`. ISOP gathers risk
signals through an **MCP server**, an **investigation planner** picks evidence-driven follow-up checks,
and a **deterministic, versioned policy** returns `ALLOW`, `REVIEW` or `REJECT` with reason codes and
a full audit trace.

> **All signals in this MVP are simulated.** Every signal carries `simulated: true` and
> `source: "mock.*"`. No third-party provider is called and no credentials are needed.

## Setup (Windows PowerShell)

```powershell
py -3.12 -m venv .venv          # or: python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

On macOS/Linux use `python3 -m venv .venv` and `.venv/bin/python` instead.

## Run

```powershell
.venv\Scripts\python -m uvicorn isop.api:app --port 8000
```

- Swagger UI: http://localhost:8000/docs
- Health: http://localhost:8000/health

On startup the API spawns the MCP server (`python -m isop.mcp_server`) as a stdio subprocess and keeps
one session open. Startup takes a few seconds while the MCP server starts. The first request takes about 300 ms (warm-up); after that, requests take about 25 ms end to end.

## Test

```powershell
.venv\Scripts\python -m pytest -v
```

The suite has 49 tests: API scenarios through the in-process MCP transport, one integration test over
the real stdio transport, orchestrator safety tests, and table-driven policy tests.

## API usage

`POST /register/check` request body:

| field | rules |
|---|---|
| `username` | 3–32 chars, `[A-Za-z0-9_.-]` |
| `email` | valid email |
| `password` | 12–128 chars. Validated, then **discarded before the investigation**; never logged or audited |
| `phone` | optional, E.164 (`+14155550123`); checked only when supplied |
| `country` | ISO-3166 alpha-2 |
| `ip_address` | IPv4/IPv6 of the end user, as observed by the calling registration service |
| `device_id` | 3–64 chars, `[A-Za-z0-9_-]` |

Unknown fields are rejected with 422. Validation errors never echo submitted values.

Sample requests (they work in PowerShell with `curl.exe` and in bash with `curl`):

```bash
curl -s -X POST http://localhost:8000/register/check -H "Content-Type: application/json" -d @examples/allow.json
```
```bash
curl -s -X POST http://localhost:8000/register/check -H "Content-Type: application/json" -d @examples/review_tor_ip.json
```
```bash
curl -s -X POST http://localhost:8000/register/check -H "Content-Type: application/json" -d @examples/review_conflicting.json
```
```bash
curl -s -X POST http://localhost:8000/register/check -H "Content-Type: application/json" -d @examples/reject_device_farm.json
```
```bash
curl -s -X POST http://localhost:8000/register/check -H "Content-Type: application/json" -d @examples/invalid.json
```

The response contains `decision`, `reason_codes`, `composite_score`, `policy_version`,
`simulated_signals` and `trace`: tools invoked with masked arguments, phase and rationale, the
signals, the rules that fired, and rejected planner steps. The same trace is available at
`GET /audit/{correlation_id}` and is appended to `var/audit.jsonl`.

## Architecture

```
POST /register/check ── FastAPI + Pydantic validation (api.py, schemas.py)
        │   password dropped here; only InvestigationSubject continues
        ▼
InvestigationService (orchestrator.py)
  1. baseline tools, run concurrently ────────────┐
  2. planner.plan_follow_ups(signals) (planner.py)│  orchestrator checks: allowlist, dedup, budget
     planner names tools only; the                │  and binds arguments from the validated request
     orchestrator binds the arguments             ▼
                                  McpToolClient (tool_client.py) ── MCP stdio ──▶ isop-signals MCP server
                                    validates output schema,                       (mcp_server.py)
                                    maps errors/timeouts to                         re-validates args
                                    ERROR/TIMEOUT signals                           └─ mock providers
  3. PolicyEngine.evaluate(signals) (policy.py + config/policy.json)                   (providers.py)
  4. AuditSink.record(trace) (audit.py): masked JSONL
```

**MCP tools:** `check_email_reputation`, `check_phone_risk`, `check_ip_risk`, `check_device_risk`
(baseline) and `check_device_history` (follow-up).

### Safety properties (each covered by tests)

- **The policy decides, not the planner.** The planner can only add follow-up evidence. It cannot
  name arguments, call tools outside `FOLLOW_UP_TOOLS`, repeat calls, or exceed `ISOP_MAX_FOLLOW_UPS`.
  Rejected steps are recorded in `trace.rejected_plan_steps`.
- **Fail closed.** A tool error, timeout, malformed output or wrong signal type becomes a
  non-`SUCCESS` signal. If a required signal is missing, rule `R001` returns at least `REVIEW`.
  A failure can never downgrade a `REJECT`.
- **Tool output is untrusted.** It is validated against `ProviderResult` (`extra="forbid"`), and its
  signal type must match the tool.
- **Minimal personal data.** The audit stores `a***@gmail.com`, `192.0.2.0/24`, `+14******23`, and a
  `sha256:` pseudonym for the device. Logs contain only tool names, scores, latencies and rule IDs.

### Policy rules (`config/policy.json`, version `reg-policy-2026.10.1`)

| rule | outcome | condition |
|---|---|---|
| R001_REQUIRED_SIGNAL_UNAVAILABLE | REVIEW | EMAIL/IP/DEVICE (and PHONE if supplied) not `SUCCESS` |
| R002_HARD_REJECT_INDICATOR | REJECT | reason code in `hard_reject_reason_codes` |
| R003_MULTIPLE_HIGH_RISK_SIGNALS | REJECT | ≥ 2 signals with score ≥ 80 |
| R004_SINGLE_HIGH_RISK_SIGNAL | REVIEW | exactly 1 signal ≥ 80 |
| R005_ELEVATED_RISK_SIGNAL | REVIEW | any signal in [60, 80) |
| R006_COMPOSITE_RISK_CRITICAL | REJECT | weighted composite ≥ 70 |
| R007_COMPOSITE_RISK_ELEVATED | REVIEW | weighted composite ≥ 45 |
| R000_NO_RISK_INDICATORS | ALLOW | nothing above fired |

Every rule is evaluated; the final decision is the most severe outcome. Change thresholds in
`config/policy.json`, or point `ISOP_POLICY_PATH` at another file. An inconsistent config (for example
elevated ≥ high) fails at startup.

### Deterministic mock data

| input | signal |
|---|---|
| email domain `mailinator.com`, `yopmail.com`, … | EMAIL 90 `DISPOSABLE_EMAIL_DOMAIN` |
| email domain `gmail.com`, `outlook.com`, … / other | EMAIL 10 / 30 (+25 if ≥ 6 digits in the local part) |
| IP `203.0.113.0/24` / `198.51.100.0/24` / `192.0.2.0/24` | IP 95 Tor / 65 hosting-proxy / 15 residential |
| phone `+1900…` / `+1555…`, `+4470…` / other | PHONE 90 premium / 65 VoIP / 10 mobile; +20 if country mismatch |
| device `emu-*` / `DEV-789`, `dev-shared-family` / other | DEVICE 85 emulator / 55 seen before / 15 new |
| device history `DEV-789` / `dev-shared-family` | 97 (17 linked accounts, 6 prior rejections) / 40 (3 linked) |

The "bad" IPs are RFC 5737 documentation ranges, so no real address is ever labelled malicious.

## Demo walkthrough (Swagger UI)

Open http://localhost:8000/docs, expand **POST /register/check** and choose **Try it out**.

1. **ALLOW:** submit the prefilled example. Four baseline tools run, no follow-up, rule `R000`, composite 12.8.
2. **REVIEW (suspicious IP):** set `ip_address` to `203.0.113.7`. The IP is high risk while the email,
   phone and device are clean, so the planner records *"Conflicting evidence … corroborate with device
   history"* and calls `check_device_history`. The result is `R004` → `REVIEW`, a high-risk signal
   that isn't enough on its own to reject.
3. **REJECT (device farm):** use `examples/reject_device_farm.json` (`device_id: DEV-789`). The baseline
   device score is only 55, which alone would not change the outcome. The planner follows up, the
   history shows 17 linked accounts, and `R002_HARD_REJECT_INDICATOR` → `REJECT`.
4. **A follow-up clears the request:** set `device_id` to `dev-shared-family`. The device was seen before,
   so the planner follows up; the history shows only 3 linked accounts, and the result is `ALLOW`.
5. **Validation:** submit `examples/invalid.json` and get a 422 that lists fields only, no echoed values.
6. **Audit:** copy a `correlation_id` into **GET /audit/{correlation_id}**, or open `var/audit.jsonl`.

## Enabling a live LLM planner later

Only `ISOP_PLANNER=deterministic` is implemented; any other value fails at startup. To add an LLM:

1. Implement `InvestigationPlanner.plan_follow_ups(evidence, completed_tools) -> list[PlannedStep]` in
   `planner.py`, for example `LlmPlanner` using the Anthropic SDK with tool use and the model read from
   an env var.
2. Pass the LLM **only** the normalized signals (type, status, score, reason codes) and the allowlisted
   tool names. Never pass the subject. Parse its answer into `PlannedStep`s; on any error or invalid
   output, return `DeterministicPlanner().plan_follow_ups(...)`.
3. Register it in `build_planner("llm")`, and read `ANTHROPIC_API_KEY` from the environment (never from code).

The orchestrator already enforces the allowlist, dedup, budget and argument binding, so an LLM planner
cannot change the decision rules. `tests/test_orchestrator.py::RoguePlanner` demonstrates this.

## Limitations (MVP)

- All providers are simulated. There is no real reputation data, and the scores are illustrative,
  not calibrated.
- There is one follow-up tool (`check_device_history`), so the planner's choices are narrow.
- No authentication, authorization or rate limiting on the API. It is intended as an internal
  service-to-service endpoint. `/audit/{id}` exposes masked traces to any caller.
- The audit log is a local JSONL file with no rotation. The in-memory lookup keeps the last 500 traces
  and is lost on restart.
- Device pseudonyms are unsalted SHA-256. Use a keyed HMAC with a managed secret in production.
- The MCP subprocess is not restarted if it dies. Calls then fail closed (`REVIEW`) until the API restarts.
- No velocity or history store: repeat registrations are not counted across requests.

## Layout

```
isop/        api, schemas, orchestrator, planner, policy, tool_client, mcp_server, providers, audit, config
config/      policy.json
examples/    sample request bodies
tests/       test_api, test_policy, test_orchestrator, test_mcp_stdio
```
