# ISOP demo checklist — 18 November 2026

Everything below works **offline**. No Claude login, Anthropic key, Hugging Face token, or internet is needed.
Claude Desktop / Claude Code is only the development tool; the app never calls it.

## Before the demo day (online, once) — done on 2026-10-24, repeat only if something was removed

- [ ] `powershell -ExecutionPolicy Bypass -File scripts\setup-online.ps1` completed without errors
- [ ] `ollama list` shows `qwen2.5:3b`
- [ ] `.venv\Scripts\python -m pytest` → all pass (Ollama smoke tests run when Ollama is up)
- [ ] Optional safety copy on a USB stick: the project folder, `%USERPROFILE%\.ollama\models`,
      `%LOCALAPPDATA%\Programs\Ollama`, and `vendor\wheels`

## Morning of 18 November

- [ ] Nothing else is listening on port 8000 (an old ISOP server from 21 Oct was still running on 24 Oct:
      `Get-NetTCPConnection -LocalPort 8000` → stop that process, or use `-Port 8001`)
- [ ] Laptop on mains power (CPU inference is much slower on battery saver)
- [ ] Close heavy apps (browsers with many tabs, Teams, IDE indexing) — the 3B model needs ~3 GB free RAM
- [ ] **Turn Wi-Fi off** to prove the offline claim (optional but convincing)
- [ ] Terminal 1: `powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1`
      → prints "Planner: LLM via Ollama, model qwen2.5:3b (present)"
- [ ] Terminal 2: `powershell -ExecutionPolicy Bypass -File scripts\verify-demo.ps1`
      → "All checks passed - ready to present." (this also warms the model so the first live request is fast)
- [ ] Browser: http://127.0.0.1:8000/ (the dashboard) and, in a second tab, http://127.0.0.1:8000/docs.
      All UI assets are bundled, so both work with Wi-Fi off. Use a full-width window: the live panel sits beside
      the form above 1100 px and below it on narrower screens.

## Live demo script (≈ 10 minutes; each LLM-planned request takes ~4–20 s on this CPU — narrate while it runs)

1. **Architecture slide** — LLM plans, MCP tools gather evidence, deterministic policy decides.
2. **Presentation setup** — click **Demo scenarios · Hide** to collapse the scenario cards so the audience sees a
   plain registration form (click **Show** to reopen; the choice is remembered). Use the **Dark mode / Light mode**
   button in the header to suit the projector; the theme is remembered too.
   **Dashboard tour** — header chips show `LLM: Ollama · qwen2.5:3b`, `Offline mode`, `MCP tools: 6`, the policy
   version. Switch **Show Intelligent Security Orchestration** on.
3. **ALLOW** — click the *Normal registration* scenario card (prefills the form), then **Register & Verify
   Security**. Watch the stages turn blue then green, the four baseline tool chips fill with scores, and the AI
   planner turn report "No further checks requested". Decision card: ALLOW, reason code `NO_RISK_INDICATORS`.
4. **REVIEW** — *Suspicious registration* (simulated Tor exit IP). The planner turn shows the tools the LLM chose
   (purple chips) and its one-line summary; follow-up chips fill in; policy stage turns amber with R004.
5. **Toggle** — while a case runs, switch the panel **off**: the form stays, the status strip keeps updating
   ("Panel hidden; the investigation continues in the backend"). Switch it back **on**: the full timeline is
   there, nothing restarted (History still shows one case).
6. **REJECT** — *High-risk registration* (`DEV-789`). Baseline device score is only 55; the LLM asks for
   `check_device_history`, which finds 17 linked accounts → `R002_HARD_REJECT_INDICATOR`, red REJECT card.
7. **Evidence** — open *Investigation Evidence*, expand `check_device_history`: sanitised request (hashed
   device ID), returned signal, **SIMULATED** badge, timing. Point out "no hidden model reasoning".
8. **History** — *Investigation History* page: filters by decision; click any row (or *Evidence* / *Decision*)
   to reopen its full orchestration timeline, rebuilt from stored events.
9. **Safety** — the LLM can only name allowlisted actions; the orchestrator fills in argument values; R008
   turns a skipped investigation into REVIEW, so a lazy or manipulated model can never cause ALLOW.
10. **Resilience (optional)** — `taskkill /IM ollama.exe /F`, submit *High-risk* again: still REJECT, planner
    labelled *Fallback rules* with the error shown. Restart with `scripts\start-demo.ps1` (or `ollama serve`).

## If something goes wrong

| Symptom | Fix |
|---|---|
| `start-demo.ps1`: "model not pulled" | Needs internet once: `ollama pull qwen2.5:3b`. Otherwise demo in fallback mode (still correct decisions). |
| First request very slow (> 60 s) | Model was cold or RAM is short. Run `verify-demo.ps1` again; close apps; or `-Deterministic`. |
| Requests show `deterministic_fallback` with `timeout` | RAM is short (seen once: a call took 137 s with 4 GB free). Close apps and re-run `verify-demo.ps1`; or raise `LLM_TIMEOUT_S` in `.env`; or `OLLAMA_MODEL=qwen2.5:1.5b` (if pulled). |
| Dashboard looks out of date after an update | Hard refresh (Ctrl+F5) once; the server now sends `Cache-Control: no-cache` so this should not recur. |
| Port 8000 busy | `scripts\start-demo.ps1 -Port 8001` and `verify-demo.ps1 -Api http://127.0.0.1:8001` |
| Everything LLM-related fails | `scripts\start-demo.ps1 -Deterministic` — full workflow, clearly labelled deterministic. |
