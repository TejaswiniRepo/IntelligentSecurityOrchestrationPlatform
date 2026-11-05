"""Generate the ISOP architecture diagrams as standalone SVG files (no dependencies, works offline).

    .venv\\Scripts\\python docs\\diagrams\\generate.py

Writes isop-high-level-architecture.svg and isop-technical-architecture.svg next to this script.
Every fact in the diagrams reflects the code in this repository (v0.3.0); update both together.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"

INK, QUIET, EDGE = "#0f172a", "#475569", "#8a97ab"
STYLES = {
    #          fill       stroke     width  dash
    "plain":   ("#ffffff", "#9aa6b8", 1.25, None),
    "ai":      ("#f4efff", "#7c3aed", 1.6, None),
    "decide":  ("#e8f0ff", "#2563eb", 2.4, None),
    "store":   ("#fff8eb", "#c27a12", 1.4, None),
    "tool":    ("#ecfaf5", "#0f8a63", 1.4, None),
    "client":  ("#ffffff", "#334155", 1.4, None),
    "optional": ("#f8fafc", "#94a3b8", 1.25, "6 5"),
}


def text(x: float, y: float, s: str, size: float = 12, weight: int = 400, fill: str = INK,
         anchor: str = "start", italic: bool = False) -> str:
    style = ' font-style="italic"' if italic else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{style}>{escape(s)}</text>')


def box(x: float, y: float, w: float, h: float, title: str, lines: list[str], kind: str = "plain",
        title_size: float = 13.5, line_size: float = 11, line_gap: float = 15.5, badge: str | None = None) -> str:
    fill, stroke, sw, dash = STYLES[kind]
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>',
           text(x + 12, y + 22, title, title_size, 650)]
    if badge:
        bw = 7 * len(badge) + 14
        out.append(f'<rect x="{x + w - bw - 10}" y="{y + 9}" width="{bw}" height="18" rx="9" fill="{stroke}" fill-opacity="0.14"/>')
        out.append(text(x + w - bw / 2 - 10, y + 22, badge, 10, 700, stroke, "middle"))
    for i, line in enumerate(lines):
        out.append(text(x + 12, y + 42 + i * line_gap, line, line_size, 400, QUIET))
    return "\n".join(out)


def arrow(d: str, both: bool = False, dashed: bool = False, color: str = EDGE, marker: str = "a") -> str:
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    start = f' marker-start="url(#{marker})"' if both else ""
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5"{dash}{start} marker-end="url(#{marker})"/>'


def chips(x: float, y: float) -> str:
    out = []
    for i, (label, fill, stroke) in enumerate([("ALLOW", "#e7f6ec", "#15803d"), ("REVIEW", "#fff4dd", "#b45309"),
                                                ("REJECT", "#fde8e7", "#c2261d")]):
        cx = x + i * 78
        out.append(f'<rect x="{cx}" y="{y}" width="70" height="22" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>')
        out.append(text(cx + 35, y + 15, label, 11, 700, stroke, "middle"))
    return "\n".join(out)


def svg(w: int, h: int, title: str, body: str, prefix: str) -> str:
    # Marker ids are prefixed so both diagrams can be inlined in one HTML page without id clashes.
    body = body.replace("url(#a)", f"url(#{prefix}-a)").replace("url(#b)", f"url(#{prefix}-b)")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{escape(title)}" font-family="{FONT}">
<title>{escape(title)}</title>
<defs>
  <marker id="{prefix}-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{EDGE}"/></marker>
  <marker id="{prefix}-b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#2563eb"/></marker>
</defs>
<rect width="{w}" height="{h}" fill="#ffffff"/>
{body}
</svg>
"""


def step(x: float, y: float, n: int) -> str:
    return (f'<circle cx="{x}" cy="{y}" r="10" fill="#2563eb"/>' + text(x, y + 4, str(n), 11, 700, "#ffffff", "middle"))


# =============================================================================== high level

def high_level() -> str:
    b = []
    b.append(text(30, 38, "ISOP at a glance: the AI plans the investigation, a deterministic policy decides", 19, 700))
    b.append(text(30, 62, "Registration fraud pre-check running entirely on one laptop. Numbered arrows follow one registration.", 12.5, 400, QUIET))

    # offline boundary
    b.append('<rect x="232" y="86" width="676" height="560" rx="14" fill="#f6f8fb" stroke="#64748b" stroke-width="1.4" stroke-dasharray="7 5"/>')
    b.append(text(250, 108, "ONE LAPTOP · FULLY OFFLINE · 127.0.0.1", 11, 700, "#475569"))

    # actor
    b.append(box(24, 270, 170, 96, "User / analyst", ["Web browser", "Registration dashboard", "Swagger UI (API docs)"], "client"))

    # components (2 columns x 3 rows)
    lx, rx, cw = 256, 600, 286
    b.append(box(lx, 124, cw, 92, "Dashboard & REST API", ["Registration form, live orchestration view,",
                                                             "investigation history  ·  FastAPI service"], "client"))
    b.append(box(rx, 124, cw, 92, "Case & audit store", ["Every investigation step stored as an event;",
                                                          "masked audit trail per case  ·  SQLite"], "store"))
    b.append(box(lx, 276, cw, 104, "Registration orchestrator", ["Runs mandatory checks, asks the planner",
                                                                  "for follow-ups, validates every step,",
                                                                  "fills in tool arguments itself"]))
    b.append(box(rx, 276, cw, 104, "AI investigation planner", ["Local LLM: Qwen2.5-3B-Instruct from the",
                                                                 "Hugging Face Hub, run by Ollama. Chooses",
                                                                 "which follow-up checks are worth running.",
                                                                 "Advises only; never decides."], "ai", badge="ADVISES"))
    b.append(box(lx, 440, cw, 104, "MCP tool server", ["Governed, read-only risk-signal tools:",
                                                        "email, phone, IP, device, device history,",
                                                        "registration history"], "tool"))
    b.append(box(rx, 440, cw, 126, "Deterministic policy engine", ["Versioned rules (R000–R008) turn the",
                                                                    "evidence into the final decision.",
                                                                    "Same evidence → same decision."], "decide", badge="DECIDES"))
    b.append(chips(rx + 28, 534))
    b.append(box(lx, 578, cw, 52, "Simulated signal providers", ["Deterministic demo data · labelled simulated"], "optional"))

    # optional cloud
    b.append(box(940, 284, 150, 88, "Optional cloud LLMs", ["Anthropic, Hugging Face", "Blocked while offline"], "optional"))

    # arrows
    b.append(arrow("M194 292 H214 V160 H256"))                       # 1 submit
    b.append(arrow("M256 186 H226 V346 H194", color="#2563eb", marker="b"))  # 7 live progress
    b.append(arrow("M399 216 V276"))                                  # 2 start case
    b.append(arrow("M399 380 V440", both=True))                       # 3 checks
    b.append(arrow("M542 328 H600", both=True))                       # 4 planner
    b.append(arrow("M542 360 H572 V502 H600"))                        # 5 evidence to policy
    b.append(arrow("M542 296 H566 V170 H600"))                        # 6 events + audit
    b.append(arrow("M399 544 V578", dashed=True))                     # tools -> providers
    b.append(arrow("M886 328 H940", dashed=True))                     # optional cloud

    b.append(step(214, 240, 1))
    b.append(step(412, 246, 2))
    b.append(step(412, 410, 3))
    b.append(step(571, 316, 4))
    b.append(step(586, 462, 5))
    b.append(step(583, 236, 6))
    b.append(step(226, 300, 7))
    b.append(text(912, 318, "✕", 14, 700, "#c2261d", "middle"))

    # flow legend
    steps = [
        "1  Submit a registration (case ID returned at once)",
        "2  Investigation starts as a server-side task",
        "3  Mandatory checks, then any follow-ups, via MCP",
        "4  Planner reads the evidence, picks follow-ups",
        "5  Policy evaluates all evidence → decision",
        "6  Each step saved as an event + masked audit",
        "7  Dashboard streams progress live (SSE)",
    ]
    b.append('<rect x="24" y="672" width="1066" height="118" rx="10" fill="#ffffff" stroke="#d5dbe5"/>')
    b.append(text(40, 694, "How one registration flows", 13, 700))
    for i, s in enumerate(steps):
        col, row = divmod(i, 4)
        b.append(text(40 + col * 520, 718 + row * 18, s, 12, 400, QUIET))
    for i, (kind, label) in enumerate([("ai", "AI component (advises)"), ("decide", "Decision owner"),
                                       ("tool", "Tool layer"), ("store", "Storage"), ("optional", "Simulated / optional")]):
        fill, stroke, sw, dash = STYLES[kind]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        b.append(f'<rect x="{850}" y="{688 + i * 19}" width="22" height="13" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
        b.append(text(880, 699 + i * 19, label, 11.5, 400, QUIET))
    return svg(1114, 812, "ISOP high-level architecture", "\n".join(b), "hl")


# =============================================================================== technical

C = [170, 484, 798, 1112]          # column x
CW = 268                            # column width
BANDS = [  # y, height, name, sub-lines
    (80, 160, "Client", ["Browser (Chrome)", "same origin only"]),
    (256, 168, "API & case runtime", ["FastAPI 0.156", "Uvicorn 0.54", "Python 3.12"]),
    (440, 182, "Orchestration & AI planning", ["asyncio", "local LLM"]),
    (638, 174, "Tools, decision & audit", ["MCP 2.3 · stdio", "policy as config"]),
    (828, 120, "Storage & configuration", ["SQLite (WAL)", ".env / env vars"]),
]


def band(y: int, h: int, name: str, sub: list[str], shade: bool) -> str:
    fill = "#f3f6fa" if shade else "#fafbfd"
    out = [f'<rect x="12" y="{y}" width="1386" height="{h}" rx="10" fill="{fill}" stroke="#dfe5ee"/>']
    words = name.split(" ")
    lines, cur = [], ""
    for w in words:  # wrap the band name into the 130 px label column
        if len(cur) + len(w) + 1 > 15 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    for i, ln in enumerate(lines):
        out.append(text(26, y + 26 + i * 17, ln, 13.5, 700))
    for i, s in enumerate(sub):
        out.append(text(26, y + 30 + len(lines) * 17 + i * 15, s, 11, 400, QUIET))
    return "\n".join(out)


def technical() -> str:
    b = [text(20, 38, "ISOP technical architecture (v0.3.0): components, interfaces and controls", 19, 700),
         text(20, 60, "One laptop, 127.0.0.1 only. The LLM can only name allowlisted follow-up actions; "
                      "the policy engine alone produces ALLOW / REVIEW / REJECT.", 12.5, 400, QUIET)]
    for i, (y, h, name, sub) in enumerate(BANDS):
        b.append(band(y, h, name, sub, i % 2 == 0))

    # ---- band 1: client
    b.append(box(C[0], 96, C[1] + CW - C[0], 128, "Registration dashboard  (/ui/)", [
        "HTML + CSS + vanilla ES modules · no build step, no CDN · strict CSP, text via textContent only",
        "app.js routing & toggles · form.js validation & Idempotency-Key · views.js + morph() keyed DOM diff",
        "store.js: per-case tracking, EventSource (SSE) with Last-Event-ID, 1 s polling fallback",
        "model.js: persisted events → stage status, timings, evidence (never fabricated progress)",
        "theme.js light/dark · orchestration panel toggle is visibility-only",
    ], "client"))
    b.append(box(C[2], 96, CW, 128, "Swagger UI  (/docs)", [
        "Swagger UI 5.34.0, bundled assets",
        "OpenAPI schema generated by FastAPI",
        "Calls the same REST API",
    ], "client"))
    b.append(box(C[3], 96, CW, 128, "Demo & operations scripts", [
        "setup-online.ps1 (the only network use)",
        "start-demo.ps1 · verify-demo.ps1 (15 checks)",
        "install_ollama_cpu.py (35 MB, signed)",
        "eval_planner.py (LLM vs rules agreement)",
    ]))

    # ---- band 2: API & runtime
    b.append(box(C[0], 272, CW, 140, "Case runtime  (cases.py)", [
        "InvestigationRunner: one asyncio task",
        "per case, independent of any browser",
        "≤ 8 concurrent → 429 + Retry-After",
        "CaseStore: cases + events (SQLite)",
        "EventHub: versioned wake-ups",
        "restart → running cases interrupted",
    ]))
    b.append(box(C[1], 272, CW, 140, "REST API  (api.py)", [
        "POST /register/check ?mode=sync|async",
        "GET /registrations · /{id} · /{id}/result",
        "GET /{id}/events?after=  (poll, rebuild)",
        "GET /{id}/stream  (SSE, Last-Event-ID)",
        "GET /health · /audit · /demo/scenarios",
        "GET /ui/ (dashboard) · /docs (Swagger)",
    ], "client"))
    b.append(box(C[2], 272, CW, 140, "Edge controls", [
        "Pydantic v2 schemas, extra=forbid",
        "422 bodies never echo submitted values",
        "Idempotency-Key: same case, or 409",
        "ASGI headers: CSP, nosniff, no-cache",
        "password validated, then discarded",
        "names never leave the API layer",
    ]))
    b.append(box(C[3], 272, CW, 140, "Optional cloud LLM providers", [
        "AnthropicProvider: official SDK,",
        "structured outputs (JSON schema)",
        "HuggingFaceProvider: router API",
        "Refused while ISOP_OFFLINE=true",
        "Never needed for the demo",
    ], "optional"))

    # ---- band 3: orchestration & planning
    b.append(box(C[0], 456, CW, 154, "InvestigationService", [
        "orchestrator.py",
        "1 baseline tools via asyncio.gather",
        "2 planner loop: ≤ 3 turns, ≤ 4 follow-ups",
        "validate: allowlist · entity · dedup · budget",
        "tool arguments bound from the request",
        "emit(type, stage, status, details)",
        "policy → audit → case.completed",
    ]))
    b.append(box(C[1], 456, CW, 154, "Investigation planner", [
        "planner.py",
        "LlmPlanner: sanitised evidence lines",
        "JSON-schema enum = remaining actions",
        "30 s cooldown after an LLM failure",
        "DeterministicPlanner: explicit rules",
        "fallback labelled deterministic_fallback",
        "never sees email, phone, IP, device",
    ], "ai"))
    b.append(box(C[2], 456, CW, 154, "LLM provider layer", [
        "llm/ (provider-neutral contract)",
        "OllamaProvider → POST /api/chat",
        "format = JSON schema (constrained)",
        "temperature 0 · fixed seed · 256 tokens",
        "hard 45 s deadline per call",
        "loopback-only while offline",
        "health(): reachable, model present",
    ], "ai"))
    b.append(box(C[3], 456, CW, 154, "Ollama 0.40.10  (CPU backend)", [
        "separate local process",
        "127.0.0.1:11434",
        "Qwen2.5-3B-Instruct GGUF · Q4_K_M · 2 GB",
        "from the Hugging Face Hub (hf.co/Qwen)",
        "median ~7 s per planning turn",
        "never pulls models at runtime",
        "keep_alive 30 min (warm model)",
    ], "ai"))

    # ---- band 4: tools, decision, audit
    b.append(box(C[0], 654, CW, 146, "MCP client  (tool_client.py)", [
        "mcp 2.3 Client, one long-lived session",
        "3 s timeout per tool call",
        "output → ProviderResult schema check",
        "reason codes ^[A-Z0-9_]{1,64}$ only",
        "error / timeout → non-SUCCESS signal",
    ], "tool"))
    b.append(box(C[1], 654, CW, 146, "MCP server  isop-signals", [
        "subprocess: python -m isop.mcp_server",
        "6 read-only tools, args re-validated",
        "baseline: email · phone · IP · device",
        "follow-up: device / registration history",
        "mock providers → simulated: true",
    ], "tool"))
    b.append(box(C[2], 654, CW, 146, "Deterministic policy engine", [
        "policy.py + config/policy.json",
        "version reg-policy-2026.11.1",
        "R000–R008 · weighted composite score",
        "R001 required signals · R008 follow-ups",
    ], "decide"))
    b.append(chips(C[2] + 18, 766))
    b.append(box(C[3], 654, CW, 146, "Audit trail  (audit.py)", [
        "AuditTrace per case, stored as JSON",
        "email a***@domain · IP /24 network",
        "phone +14******99 · device sha256:…",
        "no passwords, names or raw IDs",
        "GET /audit · /audit/{id}",
    ], "store"))

    # ---- band 5: storage & config
    b.append(box(C[0], 842, C[1] + CW - C[0], 96, "Configuration & environment", [
        ".env + environment → Settings (frozen; API keys hidden from repr) · .env.example has no secrets",
        "LLM_PROVIDER=ollama · LLM_FALLBACK=deterministic · ISOP_OFFLINE=true · LLM_TIMEOUT_S=45",
        "Python 3.12 venv · pinned requirements.txt · offline wheel cache vendor/wheels",
    ]))
    b.append(box(C[2], 842, C[3] + CW - C[2], 96, "SQLite database  var/audit.db  (WAL)", [
        "registration_case: status, decision, planner mode, idempotency key, payload hash (no password)",
        "case_event: event_id, seq, type, stage, status, ts, sanitised details",
        "audit_trace: masked AuditTrace JSON per case",
    ], "store"))

    # ---- connectors
    mid = lambda col: C[col] + CW / 2  # noqa: E731
    b.append(arrow(f"M{mid(1)} 224 V272", both=True))
    b.append(text(mid(1) + 8, 252, "HTTP JSON  ·  SSE", 11, 600, QUIET))
    b.append(arrow(f"M{mid(2)} 224 V272"))
    b.append(text(mid(2) + 8, 252, "HTTP", 11, 600, QUIET))
    b.append(arrow(f"M{C[2]} 342 H{C[1] + CW}", both=True))
    b.append(arrow(f"M{C[1]} 342 H{C[0] + CW}"))
    b.append(text(C[0] + CW + 23, 334, "start", 10.5, 600, QUIET, "middle"))
    b.append(arrow(f"M{C[0] + 70} 412 V456"))
    b.append(text(C[0] + 78, 438, "execute(case)", 10.5, 600, QUIET))
    b.append(arrow(f"M{C[0] + 200} 456 V412"))
    b.append(text(C[0] + 208, 438, "emit(event)", 10.5, 600, QUIET))
    b.append(arrow(f"M{C[0] + CW} 530 H{C[1]}", both=True))
    b.append(text(C[0] + CW + 23, 522, "plan()", 10.5, 600, QUIET, "middle"))
    b.append(arrow(f"M{C[1] + CW} 530 H{C[2]}", both=True))
    b.append(text(C[1] + CW + 23, 522, "JSON", 10.5, 600, QUIET, "middle"))
    b.append(arrow(f"M{C[2] + CW} 530 H{C[3]}", both=True))
    b.append(text(C[2] + CW + 23, 522, "HTTP", 10.5, 600, QUIET, "middle"))
    b.append(arrow(f"M{C[2] + CW - 30} 456 V432 H{mid(3)} V412", dashed=True))
    b.append(text(C[2] + CW + 52, 427, "✕ blocked offline", 10.5, 700, "#c2261d"))
    b.append(arrow(f"M{mid(0)} 610 V654"))
    b.append(text(mid(0) + 8, 634, "call(tool, args)", 10.5, 600, QUIET))
    b.append(arrow(f"M{C[0] + CW} 726 H{C[1]}", both=True))
    b.append(text(C[0] + CW + 23, 718, "stdio", 10.5, 600, QUIET, "middle"))
    b.append(arrow(f"M{C[0] + CW - 20} 610 V630 H{mid(2)} V654"))
    b.append(arrow(f"M{mid(2)} 630 H{mid(3)} V654"))
    b.append(text(mid(2) - 8, 626, "evaluate(signals)", 10.5, 600, QUIET, "end"))
    b.append(text(mid(3) - 8, 626, "record(trace)", 10.5, 600, QUIET, "end"))
    b.append(arrow(f"M{mid(3)} 800 V842"))
    b.append(text(mid(3) + 8, 824, "INSERT", 10.5, 600, QUIET))

    # ---- legend
    y = 966
    items = [("client", "Client / API"), ("ai", "AI planning (advises only)"), ("tool", "MCP tool layer"),
             ("decide", "Decision owner"), ("store", "Persistence"), ("optional", "Optional, off in offline mode")]
    x = 20
    for kind, label in items:
        fill, stroke, sw, dash = STYLES[kind]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        b.append(f'<rect x="{x}" y="{y}" width="22" height="13" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
        b.append(text(x + 30, y + 11, label, 11.5, 400, QUIET))
        x += 34 + int(len(label) * 6.6) + 24
    return svg(1410, 990, "ISOP technical architecture", "\n".join(b), "tech")


def embed(page: Path, name: str, markup: str) -> None:
    """Replace the SVG between <!-- BEGIN:name --> and <!-- END:name --> in the HTML page (self-contained)."""
    html = page.read_text(encoding="utf-8")
    start, end = f"<!-- BEGIN:{name} -->", f"<!-- END:{name} -->"
    i, j = html.index(start) + len(start), html.index(end)
    inline = markup.replace(' width="', ' data-w="', 1).replace(' height="', ' data-h="', 1)  # scale to the page width
    page.write_text(html[:i] + "\n" + inline + html[j:], encoding="utf-8")


if __name__ == "__main__":
    diagrams = {"high-level": high_level(), "technical": technical()}
    (OUT / "isop-high-level-architecture.svg").write_text(diagrams["high-level"], encoding="utf-8")
    (OUT / "isop-technical-architecture.svg").write_text(diagrams["technical"], encoding="utf-8")
    page = OUT.parent / "ISOP-Architecture-Diagrams.html"
    for name, markup in diagrams.items():
        embed(page, name, markup)
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.svg"))), "and embedded both in", page.name)
