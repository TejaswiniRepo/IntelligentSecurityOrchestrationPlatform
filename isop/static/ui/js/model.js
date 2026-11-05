// @ts-check
/**
 * Pure derivation of the orchestration view from backend events. Nothing here invents progress:
 * every stage status, timestamp, duration and message comes from a persisted event.
 *
 * @typedef {import("./api.js").CaseEvent} CaseEvent
 * @typedef {"pending"|"running"|"completed"|"skipped"|"failed"|"review"|"rejected"} StageStatus
 *
 * @typedef {Object} Stage
 * @property {string} key
 * @property {string} title
 * @property {string} description
 * @property {StageStatus} status
 * @property {string|null} startedAt
 * @property {string|null} endedAt
 * @property {number|null} durationMs
 * @property {string} result
 *
 * @typedef {Object} ToolRun
 * @property {string} key
 * @property {string} tool
 * @property {string|null} entity
 * @property {string} phase
 * @property {string} requestedBy
 * @property {string} rationale
 * @property {Record<string,string>} args
 * @property {string} startedAt
 * @property {string|null} endedAt
 * @property {"running"|"completed"|"failed"} status
 * @property {Record<string, any>|null} signal
 */

/** @type {{key:string, title:string, description:string}[]} */
export const STAGES = [
  { key: "request", title: "Registration request received", description: "Case created and registration fields validated." },
  { key: "context", title: "Context preparation", description: "Identifiers masked for audit; the planner sees risk signals only." },
  { key: "baseline", title: "Baseline MCP checks", description: "Policy-mandated checks, run in parallel through the MCP server." },
  { key: "planner", title: "AI investigation planner", description: "Reviews the evidence and decides whether more investigation is needed." },
  { key: "follow_up", title: "Follow-up investigation", description: "Tools selected by the planner, validated against the allowlist." },
  { key: "aggregation", title: "Signal aggregation", description: "Collected evidence normalised and consolidated." },
  { key: "policy", title: "Deterministic risk policy", description: "Versioned rules R000–R008 produce the decision." },
  { key: "decision", title: "Final decision", description: "Decision written to the masked audit trail and returned." },
];

export const TOOL_LABELS = {
  check_email_reputation: "Email reputation",
  check_phone_risk: "Phone risk",
  check_ip_risk: "IP risk",
  check_device_risk: "Device risk",
  check_device_history: "Device history",
  get_registration_history: "Registration history",
};

/** @param {string} tool @param {string|null} [entity] */
export function toolLabel(tool, entity) {
  const base = /** @type {Record<string,string>} */ (TOOL_LABELS)[tool] || tool;
  return entity ? `${base} (${entity})` : base;
}

const BY_TEXT = {
  "policy-baseline": "policy baseline",
  llm: "selected by the AI planner",
  deterministic: "selected by the rules planner",
  deterministic_fallback: "selected by fallback rules (LLM unavailable)",
};

const STOP_TEXT = {
  planner_done: "Planner reported that the evidence is sufficient.",
  no_remaining_actions: "Every follow-up tool has already run.",
  budget_exhausted: "Follow-up budget reached.",
  max_iterations: "Planning turn limit reached.",
  no_valid_steps: "No proposed step passed validation.",
  planner_error: "Planner error: investigation stopped; policy decides on the evidence gathered.",
};

/** @param {string|null} a @param {string|null} b */
const span = (a, b) => (a && b ? Math.max(0, Date.parse(b) - Date.parse(a)) : null);

/** @param {Record<string, any>} s */
function signalText(s) {
  if (s.status !== "SUCCESS") return `${s.status}${s.error ? `: ${s.error}` : ""}`;
  return `${s.signal_type} risk ${s.risk_score} (${(s.reason_codes || []).join(", ") || "no codes"})`;
}

/**
 * @param {CaseEvent[]} events
 * @param {import("./api.js").CaseStatus} [caseStatus]
 */
export function deriveCase(events, caseStatus) {
  /** @type {Record<string, Stage>} */
  const stages = {};
  for (const s of STAGES) stages[s.key] = { ...s, status: "pending", startedAt: null, endedAt: null, durationMs: null, result: "" };
  /** @type {ToolRun[]} */
  const tools = [];
  /** @type {any[]} */
  const turns = [];
  /** @type {{step:string, reason:string, ts:string}[]} */
  const rejectedSteps = [];
  /** @type {{ts:string, text:string, tone:string, key:string}[]} */
  const activity = [];
  /** @type {any} */ let policy = null;
  /** @type {any} */ let decision = null;
  /** @type {string|null} */ let failure = null;
  /** @type {any} */ let context = null;
  /** @type {any} */ let aggregation = null;
  /** @type {any} */ let received = null;

  /** @param {string} key @param {string} ts */
  const begin = (key, ts) => { const st = stages[key]; if (!st.startedAt) st.startedAt = ts; if (st.status === "pending") st.status = "running"; };
  /** @param {string} key @param {StageStatus} status @param {string} ts @param {string} [result] */
  const end = (key, status, ts, result) => {
    const st = stages[key]; if (!st.startedAt) st.startedAt = ts;
    st.status = status; st.endedAt = ts; st.durationMs = span(st.startedAt, ts);
    if (result !== undefined) st.result = result;
  };
  /** @param {CaseEvent} e @param {string} text @param {string} [tone] */
  const say = (e, text, tone = "info") => activity.push({ ts: e.ts, text, tone, key: `${e.event_id}-${activity.length}` });

  for (const e of events) {
    const d = e.details || {};
    switch (e.type) {
      case "case.received":
        received = d;
        end("request", "completed", e.ts, `Case ${d.case_id} · ${(d.fields_validated || []).length} fields validated`);
        say(e, `Registration request received. Case ${d.case_id} created; ${(d.fields_validated || []).length} fields validated.`);
        if (d.password_received_and_discarded) say(e, "A password was supplied; it was discarded without being stored or logged.");
        break;
      case "context.prepared":
        context = d;
        end("context", "completed", e.ts, `Planner: ${d.planner_provider}${d.planner_model ? ` · ${d.planner_model}` : ""}`);
        say(e, "Context prepared: identifiers masked; the planner receives risk signals only, never raw identifiers.");
        break;
      case "baseline.started":
        begin("baseline", e.ts);
        say(e, `Running ${(d.tools || []).length} policy-mandated baseline checks through the MCP server.`);
        for (const sk of d.skipped || []) say(e, `Skipped ${sk}.`, "muted");
        break;
      case "baseline.completed":
        end("baseline", d.failed ? "failed" : "completed", e.ts, `${d.succeeded} succeeded${d.failed ? `, ${d.failed} failed` : ""}`);
        break;
      case "tool.started": {
        const key = `${d.tool}:${d.entity || ""}`;
        if (e.stage === "follow_up") begin("follow_up", e.ts);
        tools.push({ key, tool: d.tool, entity: d.entity || null, phase: e.stage, requestedBy: d.requested_by,
          rationale: d.rationale, args: d.arguments_masked || {}, startedAt: e.ts, endedAt: null, status: "running", signal: null });
        const by = /** @type {Record<string,string>} */ (BY_TEXT)[d.requested_by] || d.requested_by;
        say(e, `${toolLabel(d.tool, d.entity)} invoked via MCP (${by}).`);
        break;
      }
      case "tool.completed": {
        const key = `${d.tool}:${d.entity || ""}`;
        const run = [...tools].reverse().find((t) => t.key === key && t.status === "running");
        if (run) { run.status = e.status === "completed" ? "completed" : "failed"; run.endedAt = e.ts; run.signal = d.signal; }
        say(e, `${toolLabel(d.tool, d.entity)} returned ${signalText(d.signal || {})}.`, e.status === "completed" ? "info" : "error");
        break;
      }
      case "planner.started":
        begin("planner", e.ts);
        turns.push({ iteration: d.iteration, startedAt: e.ts, endedAt: null, provider: d.provider, model: d.model,
          available: d.available_actions || [], mode: null, llmStatus: null, latencyMs: null, proposed: [], assessment: null, error: null, done: false });
        say(e, d.provider === "deterministic"
          ? `Rules planner evaluating ${d.evidence_signals} signals (turn ${d.iteration}; no LLM in this mode).`
          : `AI agent is evaluating ${d.evidence_signals} signals with ${d.provider}${d.model ? ` · ${d.model}` : ""} (turn ${d.iteration}).`);
        break;
      case "planner.completed": {
        const t = turns.find((x) => x.iteration === d.iteration && !x.endedAt) || turns[turns.length - 1];
        if (t) Object.assign(t, { endedAt: e.ts, mode: d.mode || null, llmStatus: d.llm_status || (e.status === "failed" ? "error" : null),
          latencyMs: d.latency_ms ?? null, proposed: d.proposed || [], assessment: d.assessment || null, error: d.error || null, done: !!d.done });
        const picks = (d.proposed || []).map((/** @type {any} */ p) => toolLabel(p.tool, p.entity));
        const list = picks.length ? picks.join(", ") : "no further checks";
        if (e.status === "failed") say(e, `Planner failed: ${d.error}`, "error");
        else if (d.mode === "llm") say(e, `Local LLM selected: ${list}.`);
        else if (d.mode === "deterministic_fallback") say(e, `LLM unavailable (${d.error || d.llm_status}); fallback rules selected: ${list}.`, "warn");
        else say(e, `Rules planner selected: ${list}.`);
        for (const p of d.proposed || []) if (p.rationale) say(e, `${toolLabel(p.tool, p.entity)} required: ${p.rationale}`, "muted");
        break;
      }
      case "plan.step_rejected":
        rejectedSteps.push({ step: d.step, reason: d.reason, ts: e.ts });
        say(e, `Orchestrator rejected planner step ${d.step}: ${d.reason}.`, "warn");
        break;
      case "planner.concluded": {
        const text = /** @type {Record<string,string>} */ (STOP_TEXT)[d.stop_reason] || d.stop_reason;
        end("planner", "completed", e.ts, `${d.turns} turn${d.turns === 1 ? "" : "s"} · ${d.follow_ups_run} follow-up${d.follow_ups_run === 1 ? "" : "s"}`);
        if (d.turns === 0) stages.planner.result = text;
        const fu = stages.follow_up;
        if (fu.status === "running") end("follow_up", tools.some((t) => t.phase === "follow_up" && t.status === "failed") ? "failed" : "completed", e.ts,
          `${tools.filter((t) => t.phase === "follow_up").length} tool call(s)`);
        say(e, text, "muted");
        break;
      }
      case "follow_up.skipped":
        end("follow_up", "skipped", e.ts, "No additional investigation required");
        break;
      case "aggregation.completed":
        aggregation = d;
        end("aggregation", "completed", e.ts, `${d.succeeded} signals${d.failed ? `, ${d.failed} unavailable` : ""}${d.highest ? ` · highest ${d.highest.signal_type} ${d.highest.risk_score}` : ""}`);
        say(e, "Investigation evidence collected. Sending findings to the deterministic policy engine.");
        break;
      case "policy.started":
        begin("policy", e.ts);
        break;
      case "policy.completed": {
        policy = d;
        const st = d.decision === "ALLOW" ? "completed" : d.decision === "REVIEW" ? "review" : "rejected";
        end("policy", st, e.ts, `${d.decision} · ${(d.rules_fired || []).map((/** @type {any} */ r) => r.rule_id.split("_")[0]).join(", ")}`);
        say(e, `Policy ${d.policy_version} evaluated: ${d.decision} (${(d.rules_fired || []).map((/** @type {any} */ r) => r.rule_id).join(", ")}).`,
          d.decision === "ALLOW" ? "success" : d.decision === "REVIEW" ? "warn" : "error");
        break;
      }
      case "audit.recorded":
        begin("decision", e.ts);
        say(e, "Masked audit trace written to SQLite.", "muted");
        break;
      case "case.completed": {
        decision = d;
        const st = d.decision === "ALLOW" ? "completed" : d.decision === "REVIEW" ? "review" : "rejected";
        end("decision", st, e.ts, d.decision);
        say(e, `Investigation complete in ${(d.duration_ms / 1000).toFixed(2)} s: ${d.decision}.`, d.decision === "ALLOW" ? "success" : d.decision === "REVIEW" ? "warn" : "error");
        break;
      }
      case "case.failed":
        failure = d.error || "investigation failed";
        for (const st of Object.values(stages)) if (st.status === "running") end(st.key, "failed", e.ts);
        end("decision", "failed", e.ts, failure || "");
        say(e, `Investigation failed: ${failure}`, "error");
        break;
      default:
        break;
    }
  }

  const status = decision ? "completed" : failure ? (caseStatus === "interrupted" ? "interrupted" : "failed") : (caseStatus || "running");
  const current = STAGES.find((s) => stages[s.key].status === "running")?.key
    || (status === "running" ? STAGES.find((s) => stages[s.key].status === "pending")?.key : null) || null;
  const firstTs = events[0]?.ts || null;
  const lastTs = events[events.length - 1]?.ts || null;
  return { stages, order: STAGES.map((s) => s.key), tools, turns, rejectedSteps, activity, policy, decision, failure,
    context, aggregation, received, status, current, startedAt: firstTs, lastAt: lastTs };
}
