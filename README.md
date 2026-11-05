# ISOP — Intelligent Security Orchestration Platform

Registration fraud pre-check with an **LLM investigation planner** that runs **fully offline**.
A registration service calls `POST /register/check`. ISOP runs mandatory risk checks through an **MCP server**;
a **local LLM (Ollama)** reads the evidence and chooses follow-up investigations; a **deterministic, versioned
policy** returns `ALLOW`, `REVIEW` or `REJECT` with reason codes and a full audit trace stored in **SQLite**.

> **All fraud signals are simulated.** Every signal carries `simulated: true` and `source: "mock.*"`.
> No third-party fraud vendor is called. **The LLM never decides** — it only picks which allowlisted
> tools to run next.

The running application does **not** depend on Claude Desktop, Claude Code, a Claude login, an Anthropic API
key, Hugging Face, or the internet. Anthropic and Hugging Face adapters exist only as optional comparison
providers.

**Dashboard:** http://127.0.0.1:8000/ (redirects to `/ui/`): a registration form, a live **Intelligent Security
Orchestration** panel (shown or hidden with a toggle that never affects the investigation), a dedicated
Security Orchestration page per case, and the Investigation History. It is plain HTML, CSS and ES modules
served by FastAPI: no build step, no npm, no CDN, so it works offline.

## Dashboard

| Page | What it shows |
|---|---|
| Dashboard | Registration form (First/Last Name, Email, Mobile, IP, Device ID, optional Fingerprint and Country), demo scenario buttons, the live orchestration panel, recent registrations |
| Security Orchestration | One case, full width: 8 stages with real start/end times and durations, tool chips with scores, LLM turns (provider, model, latency, the model's one-line summary and chosen tools), agent activity feed, Investigation Evidence (each MCP call: sanitised request, returned signal, Simulated badge, timing, errors), final decision card |
| Investigation History | Every case with masked email, status, decision, planner mode, duration; filters; actions open the investigation, its evidence or its decision |

How live progress works: the form posts `POST /register/check?mode=async` with an `Idempotency-Key`; the backend
returns the case ID at once and runs the investigation as a server-side task. The orchestrator writes each step
as an event to SQLite; the browser loads them with `GET /registrations/{id}/events` and then follows
`GET /registrations/{id}/stream` (Server-Sent Events, resumed with `Last-Event-ID` after a disconnect), falling back
to polling every second if the stream keeps failing. A page refresh rebuilds the timeline from the stored events
and reconnects. Hiding the panel only stops drawing it; the stream, the store and the investigation continue.

Stage order is the real execution order: request → context preparation → **policy-mandated baseline checks**
(email, IP, device, and phone when supplied) → **AI planner** (decides whether more investigation is needed) →
**LLM-selected follow-ups** → aggregation → deterministic policy → decision. The baseline checks are deliberately not
left to the LLM: the policy requires them on every case (R001), so letting the model skip one would only turn the
result into REVIEW after an extra LLM call.

## Architecture

```
Swagger UI (bundled, offline) / curl
        │ POST /register/check
        ▼
FastAPI + Pydantic validation ── password validated, then dropped (never reaches tools, LLM, logs, audit)
        ▼
InvestigationService (orchestrator.py)
  1. Baseline MCP checks (policy-mandated, concurrent): email, phone, IP, device
  2. Planner loop (≤ LLM_MAX_ITERATIONS, ≤ ISOP_MAX_FOLLOW_UPS tool calls)
       planner ──► LlmPlanner ──► LLM provider layer ─┬─ ollama        (default, local 127.0.0.1)
          │            │ on failure                  ├─ anthropic     (optional, cloud, off when offline)
          │            ▼                             └─ huggingface   (optional, cloud, off when offline)
          │        DeterministicPlanner (labelled "deterministic_fallback")
          ▼
       orchestrator validates each step: allowlist, entity, dedup, budget; binds argument values itself
          ▼
       MCP client ── stdio ──► isop-signals MCP server ──► deterministic mock providers
          ▼                       check_email_reputation · check_phone_risk · check_ip_risk ·
       results go back to         check_device_risk · check_device_history · get_registration_history
       the planner (next turn)
  3. PolicyEngine (config/policy.json) ──► ALLOW | REVIEW | REJECT + rule IDs + reason codes
  4. AuditSink ──► var/audit.db (SQLite, masked identifiers) + JSON response
```

### What the LLM can and cannot do

| The LLM **can** | The LLM **cannot** |
|---|---|
| Choose `check_device_history` and/or `get_registration_history` (entity `ip`, `phone`, `email`) | Call any other tool (`FOLLOW_UP_TOOLS` allowlist; Ollama decoding is also schema-constrained to it) |
| Decide when it has enough evidence (`done: true`) | Supply argument values — the orchestrator binds email/phone/IP/device from the validated request |
| Give a short rationale (stored in the audit trace, labelled LLM) | See raw identifiers — it gets only signal type, status, score, reason codes, scalar details |
| | Exceed the iteration/tool budget, repeat a call, or produce a decision (extra fields are discarded) |
| | Cause `ALLOW` by skipping a needed check — rule **R008** turns a missing required follow-up into `REVIEW` |

### Policy rules (`config/policy.json`, version `reg-policy-2026.11.1`)

| rule | outcome | condition |
|---|---|---|
| R001_REQUIRED_SIGNAL_UNAVAILABLE | REVIEW | EMAIL/IP/DEVICE (and PHONE if supplied) not `SUCCESS` (error/timeout ≠ low risk) |
| R002_HARD_REJECT_INDICATOR | REJECT | reason code in `hard_reject_reason_codes` (e.g. `DEVICE_LINKED_TO_MANY_ACCOUNTS`) |
| R003_MULTIPLE_HIGH_RISK_SIGNALS | REJECT | ≥ 2 signals with score ≥ 80 |
| R004_SINGLE_HIGH_RISK_SIGNAL | REVIEW | exactly 1 signal ≥ 80 (one indicator alone is not proof) |
| R005_ELEVATED_RISK_SIGNAL | REVIEW | any signal in [60, 80) |
| R006_COMPOSITE_RISK_CRITICAL | REJECT | weighted composite ≥ 70 |
| R007_COMPOSITE_RISK_ELEVATED | REVIEW | weighted composite ≥ 45 |
| R008_REQUIRED_FOLLOW_UP_MISSING | REVIEW | device ≥ 40 without device history; IP/phone ≥ 60 without registration history for that entity |
| R000_NO_RISK_INDICATORS | ALLOW | nothing above fired |

All rules are evaluated; the final decision is the most severe outcome. Thresholds, weights, hard-reject codes
and follow-up requirements are configuration; an inconsistent config fails at startup.

### Deterministic mock data (simulated)

| input | signal |
|---|---|
| email `mailinator.com`, `yopmail.com`, … / `gmail.com`, `outlook.com`, … / other | 90 disposable / 10 established / 30 unknown (+25 if ≥ 6 digits in local part) |
| IP `203.0.113.0/24` / `198.51.100.0/24` / `192.0.2.0/24` (RFC 5737 documentation ranges) | 95 Tor exit / 65 hosting-proxy / 15 residential |
| phone `+1900…` / `+1555…`, `+4470…` / other | 90 premium / 65 VoIP / 10 mobile; +20 on country mismatch |
| device `emu-*` / `DEV-789`, `dev-shared-family` / other | 85 emulator / 55 seen before / 15 new |
| device history `DEV-789` / `dev-shared-family` | 97 (17 linked accounts, 6 prior rejections) / 40 (3 linked) |
| registration history: IP `198.51.100.0/24` 9 in 24 h, `203.0.113.0/24` 2; phone `+15555550100` 7, `+15555550123` 2 | ≥ 15 → 90, ≥ 5 → 70, ≥ 2 → 30, else 5 |

## Local model

| item | value |
|---|---|
| Runtime | Ollama **0.40.10**, CPU backend only (`%LOCALAPPDATA%\Programs\Ollama`, 77 MB) |
| Model | **`qwen2.5:3b`** (Q4_K_M, 1.9 GB) — `OLLAMA_MODEL` in `.env` |
| Why this model | Machine: i5-12500H, 15.6 GB RAM, Intel Iris Xe (no CUDA), ~7 GB free disk. A 3B model fits RAM and disk and runs on CPU; 7B+ would not. Qwen2.5 follows JSON schemas well and has no slow "thinking" mode. |
| Smaller fallback | `qwen2.5:1.5b` (986 MB) — set `OLLAMA_MODEL=qwen2.5:1.5b` after `ollama pull qwen2.5:1.5b` |
| Interface | `POST http://127.0.0.1:11434/api/chat` with `format=<JSON schema>` (grammar-constrained structured output), `temperature=0`, fixed `seed` |
| Offline guard | `ISOP_OFFLINE=true` (default) refuses cloud providers and any non-loopback `OLLAMA_BASE_URL`; the app never calls `/api/pull` |

The full Ollama installer is 1.6 GB (mostly NVIDIA CUDA libraries this laptop cannot use), more than the
disk could spare. `scripts/install_ollama_cpu.py` instead reads the **official release zip** with HTTP range
requests and extracts only `ollama.exe` and the CPU backends (~35 MB downloaded); the binary's Authenticode
signature (Ollama Inc.) is verified by `setup-online.ps1`. On a machine with more disk, the official installer
(`winget install Ollama.Ollama`) works the same way.

## Setup (once, needs internet)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-online.ps1
```

Equivalent manual steps:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
.venv\Scripts\python -m pip download -r requirements.txt -d vendor\wheels --only-binary=:all:   # offline reinstall cache
.venv\Scripts\python scripts\install_ollama_cpu.py          # or: winget install Ollama.Ollama
$env:OLLAMA_HOST="127.0.0.1:11434"; & "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve   # separate terminal
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull qwen2.5:3b
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" run qwen2.5:3b "Reply with OK"              # verify inference
Copy-Item .env.example .env
```

## Run (offline)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1                 # LLM via Ollama
powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1 -Deterministic  # no LLM at all
powershell -ExecutionPolicy Bypass -File scripts\verify-demo.ps1                # second terminal: PASS/FAIL checks
```

`start-demo.ps1` starts Ollama on 127.0.0.1 if needed (never pulls), reports whether the model is present, then
runs `uvicorn isop.api:app --host 127.0.0.1 --port 8000`.

- Dashboard: http://127.0.0.1:8000/ (or `/ui/`)
- Swagger UI: http://127.0.0.1:8000/docs (assets bundled under `isop/static/swagger`, works without internet)

## Test

```powershell
.venv\Scripts\python -m pytest                       # all offline tests; Ollama smoke tests auto-skip if no model
.venv\Scripts\python -m pytest tests\unit            # policy, planner, providers (mocked HTTP), config
.venv\Scripts\python -m pytest tests\integration     # API end to end, MCP stdio, offline guarantees, safety
.venv\Scripts\python -m pytest tests\integration\test_ollama_smoke.py -v -s   # real local inference
$env:ISOP_RUN_CLOUD_TESTS="1"; .venv\Scripts\python -m pytest tests\cloud     # optional, paid, needs keys
```

| folder | contents |
|---|---|
| `tests/unit` | policy rule table incl. R008, LLM planner with a scripted fake model (fallback, cooldown, injected fields), Ollama/HF adapters against mocked HTTP, offline config enforcement |
| `tests/integration` | API scenarios, real MCP stdio server, unavailable/slow MCP tools, LLM-selected tools executed and attributed, LLM cannot force ALLOW or REJECT, Ollama down → labelled fallback, `LLM_FALLBACK=error` → 503, socket guard proving no external connection, bundled Swagger |
| `tests/integration/test_cases_api.py` | async cases, event order and sanitisation, SSE delivery + `Last-Event-ID` resume, polling cursor, idempotency (duplicate → same case, changed payload → 409), concurrent cases keep their own events, model failure → failed case, restart → interrupted, concurrency cap → 429, demo scenarios, dashboard headers (strict CSP, no CDN), no lost wake-ups in the event hub |
| `tests/integration/test_ollama_smoke.py` | real inference against the local model (skipped when Ollama/model absent) |
| `tests/cloud` | Anthropic / Hugging Face smoke tests (skipped unless `ISOP_RUN_CLOUD_TESTS=1` and keys set) |

## API

`POST /register/check` — body fields: `first_name`, `last_name` (optional, letters/space/-'.), `email`, `phone`
(optional, E.164), `country` (optional, ISO alpha-2; without it the phone–country rule is skipped), `ip_address`,
`device_id`, `device_fingerprint` (optional; recorded only as a hash), plus legacy optional `username` and `password`
(12–128; discarded after validation, never stored). Unknown fields → 422; 422 bodies never echo submitted values.

Query `mode=sync` (default) waits and returns the full decision. `mode=async` returns `202`
`{case_id, status, created, links}` at once. Send `Idempotency-Key` (8–64 chars) to make retries safe: the same key
returns the original case; the same key with a different payload returns `409`. At most 8 investigations run at
once (`429` + `Retry-After` beyond that).

| Endpoint | Purpose |
|---|---|
| `GET /registrations?limit=50` | cases, newest first (masked email, status, decision, planner mode, duration) |
| `GET /registrations/{case_id}` | one case + links |
| `GET /registrations/{case_id}/events?after=<event_id>` | persisted events (polling and rebuild after refresh) |
| `GET /registrations/{case_id}/stream` | Server-Sent Events: `progress` events, then `end`; honours `Last-Event-ID` |
| `GET /registrations/{case_id}/result` | 200 final decision · 202 still running · 409 failed/interrupted |
| `GET /demo/scenarios` | the four deterministic demo registrations |

Event shape: `{event_id, case_id, seq, type, stage, status, ts, details}`. Types: `case.received`,
`context.prepared`, `baseline.started`/`completed`, `tool.started`/`completed`, `planner.started`/`completed`/
`concluded`, `plan.step_rejected`, `follow_up.skipped`, `aggregation.completed`, `policy.started`/`completed`,
`audit.recorded`, `case.completed` | `case.failed`. Details hold masked identifiers and signal summaries only.

Response: `decision`, `reason_codes`, `composite_score`, `policy_version`, `simulated_signals`, **`planner_mode`**
(`llm` | `deterministic` | `deterministic_fallback` | `mixed` | `none`) and `trace`:
`investigation.turns[]` (per planning turn: mode, provider, model, status, latency, error, proposed steps),
`tools_invoked[]` (tool, entity, phase, `requested_by`, rationale, masked arguments, status, latency),
`signals[]`, `policy.rules_fired[]`, `rejected_plan_steps[]`.

Other endpoints: `GET /health` (config, Ollama reachability and model presence, MCP tools, policy version),
`GET /audit?limit=20`, `GET /audit/{correlation_id}`.

```bash
curl.exe -s -X POST http://127.0.0.1:8000/register/check -H "Content-Type: application/json" -d "@examples/reject_device_farm.json"
```

Sample bodies: `examples/allow.json`, `review_tor_ip.json`, `review_conflicting.json`, `reject_device_farm.json`, `invalid.json`.

## Configuration

See `.env.example`. Key settings: `LLM_PROVIDER` (`ollama` default | `deterministic` | `anthropic` | `huggingface`),
`LLM_FALLBACK` (`deterministic` default | `error`), `ISOP_OFFLINE` (`true` default), `OLLAMA_MODEL`,
`LLM_TIMEOUT_S`, `LLM_TEMPERATURE`, `LLM_MAX_ITERATIONS`, `LLM_COOLDOWN_S`, `ISOP_MAX_FOLLOW_UPS`.

Optional cloud providers (need `ISOP_OFFLINE=false`, internet, and credentials):
- **anthropic** — `pip install -e ".[cloud]"`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (default `claude-opus-5-5`).
  A Claude Max subscription does **not** provide API access; API usage is billed separately.
- **huggingface** — `HF_TOKEN`, `HF_MODEL`. JSON-schema output support depends on the routed provider.

Neither cloud adapter has been exercised against the live service in this project (no credentials were
used); they are covered by mocked tests only.

## Measured on this laptop (2026-10-24, CPU only, `qwen2.5:3b`)

`scripts\verify-demo.ps1` (real model, MCP over stdio): all 13 checks passed. Requests took 3.6 s (ALLOW, one LLM
turn) to 18.4 s (REJECT, multiple LLM turns); warm-up inference 0.9 s after the model is loaded.

`scripts\eval_planner.py --runs 2` (8 scenarios x 2 runs, LLM first turn vs deterministic rules, same policy):

| metric | result |
|---|---|
| final decision agreement | **15/16** |
| identical tool choices | 8/16 |
| extra / missed follow-up calls | 5 / 4 |
| LLM latency per planning turn | median 7.2 s, max 11.0 s |

The single decision disagreement was conservative: the model skipped `check_device_history` for a seen-before
device and R008 produced REVIEW instead of ALLOW. Output is not perfectly repeatable on CPU even at
temperature 0 with a fixed seed. Re-run the harness with `--model <name>` to compare other local models.

Resilience (verified live): with Ollama killed, `reject_device_farm.json` still returned REJECT in 2.2 s labelled
`deterministic_fallback` with the error recorded; after restarting Ollama and the 30 s cooldown the next request
used the LLM again without restarting the API.

## Demo walkthrough

See **[DEMO_CHECKLIST.md](DEMO_CHECKLIST.md)** for the 18 November 2026 checklist and live script.

## Restoring the environment on this computer

| lost | restore (offline) | restore (online) |
|---|---|---|
| `.venv` | `py -3.12 -m venv .venv; .venv\Scripts\python -m pip install --no-index --find-links vendor\wheels -r requirements.txt setuptools wheel; .venv\Scripts\python -m pip install --no-index -e . --no-deps --no-build-isolation` (verified: fresh venv from the cache passed the full suite) | `scripts\setup-online.ps1` |
| Ollama runtime | copy back `%LOCALAPPDATA%\Programs\Ollama` from backup | `python scripts\install_ollama_cpu.py` |
| model | copy back `%USERPROFILE%\.ollama\models` from backup | `ollama pull qwen2.5:3b` |

Portability: verified only on this Windows 11 laptop. A different computer needs Python 3.12 and the downloads
above **in advance**; it has not been tested elsewhere.

## Limitations

- Signals are simulated and scores illustrative, not calibrated. No real fraud vendor is integrated.
- CPU-only inference: 5–11 s per LLM planning turn; a request with two turns takes up to ~20 s.
- Small local model: tool choices agree with the rules planner only about half the time (decisions 15/16);
  R008 and the policy bound the impact. Requests are processed one LLM call at a time (no concurrency tuning).
- With `LLM_FALLBACK=error`, a 503 response is not written to the audit store.
- No API authentication/rate limiting (internal service-to-service MVP); `/audit` exposes masked traces.
- Device pseudonyms are unsalted SHA-256 (use a keyed HMAC in production). SQLite audit has no retention policy.
- The MCP subprocess is not restarted if it dies; calls then fail closed (REVIEW) until the API restarts.
- Run one server per audit database: on startup a server marks every still-`running` case in its database as
  `interrupted` (correct after a crash, wrong if a second server shares the file). Use `ISOP_AUDIT_DB` to separate them.
- Under memory pressure CPU inference slows sharply; one LLM call once took 137 s. Each call now has a hard
  45 s deadline (`LLM_TIMEOUT_S`), after which the turn falls back to the labelled rules planner.

## Layout

```
isop/            api, schemas, orchestrator, planner, policy, tool_client, mcp_server, providers, audit, cases, demo, config
isop/llm/        base (contract), ollama, anthropic_provider, huggingface, build_llm_provider (offline guard)
isop/static/ui/  dashboard: index.html, css/app.css, js/{app,api,store,model,views,form,dom}.js
isop/static/     bundled Swagger UI 5.34.0 (Apache-2.0)
config/          policy.json
scripts/         setup-online.ps1, start-demo.ps1, verify-demo.ps1, install_ollama_cpu.py
examples/        sample request bodies
tests/           unit/, integration/, cloud/
```
