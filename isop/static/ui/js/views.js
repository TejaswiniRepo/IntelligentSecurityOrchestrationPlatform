// @ts-check
/** Render functions: state in, DOM out. Stateless; `morph` applies the result to the page. */
import { clock, dateTime, duration, h, icon } from "./dom.js";
import { prettyModel, toolLabel } from "./model.js";
import { state } from "./store.js";

/** @typedef {import("./store.js").TrackedCase} TrackedCase */

const STATUS_TEXT = { pending: "Pending", running: "In progress", completed: "Completed", skipped: "Skipped",
  failed: "Failed", review: "Requires review", rejected: "Rejected", interrupted: "Interrupted" };

/** @param {string} status */
export function statusPill(status) {
  return h("span", { class: ["pill", `pill-${status}`] }, /** @type {Record<string,string>} */ (STATUS_TEXT)[status] || status);
}

/** @param {string|null|undefined} decision */
export function decisionPill(decision) {
  if (!decision) return h("span", { class: "muted" }, "—");
  return h("span", { class: ["decision-pill", `decision-${decision.toLowerCase()}`] }, decision);
}

const MODE_TEXT = { llm: "LLM", deterministic: "Rules", deterministic_fallback: "Fallback rules", mixed: "Mixed", none: "No planning" };
/** @param {string|null|undefined} mode */
export function modeBadge(mode) {
  if (!mode) return h("span", { class: "muted" }, "—");
  return h("span", { class: ["mode", `mode-${mode}`] }, /** @type {Record<string,string>} */ (MODE_TEXT)[mode] || mode);
}

/** @param {string} status */
function stageIcon(status) {
  const name = status === "completed" ? "check" : status === "failed" || status === "rejected" ? "x"
    : status === "review" ? "alert" : status === "skipped" ? "chevron" : status === "running" ? "dot" : "dot";
  return h("span", { class: ["stage-dot", `dot-${status}`] }, icon(/** @type {any} */ (name), "icon-sm"));
}

const STAGE_ICONS = { request: "inbox", context: "layers", baseline: "shield", planner: "ai", follow_up: "flow",
  aggregation: "db", policy: "scale", decision: "check" };

/** @param {string} tool */
function toolIcon(tool) {
  const name = tool.includes("email") ? "mail" : tool.includes("phone") ? "phone" : tool.includes("ip") ? "globe"
    : tool.includes("registration") ? "history2" : "device";
  return icon(/** @type {any} */ (name), "icon-sm");
}

/** @param {import("./model.js").ToolRun} t */
function toolChip(t) {
  const s = t.signal;
  const score = s && s.status === "SUCCESS" ? `${s.risk_score}` : s ? s.status : "…";
  const tone = t.status === "running" ? "running" : t.status === "failed" ? "failed"
    : (s?.risk_score ?? 0) >= 80 ? "high" : (s?.risk_score ?? 0) >= 60 ? "elevated" : "low";
  return h("div", { class: ["tool-chip", `tool-${tone}`], dataset: { key: `chip-${t.key}` }, title: t.tool },
    toolIcon(t.tool),
    h("span", { class: "tool-chip-name" }, toolLabel(t.tool, t.entity)),
    h("span", { class: "tool-chip-score" }, t.status === "running" ? h("span", { class: "spinner spinner-xs" }) : score));
}

/** @param {any} t */
function turnBlock(t) {
  const mode = t.mode || "running";
  const who = t.mode === "llm" ? `${t.provider} · ${prettyModel(t.model)}` : t.mode === "deterministic_fallback"
    ? `fallback rules (LLM: ${t.llmStatus || "error"})` : t.mode === "deterministic" ? "deterministic rules" : t.provider;
  return h("div", { class: "turn", dataset: { key: `turn-${t.iteration}` } },
    h("div", { class: "turn-head" },
      h("span", { class: "turn-n" }, `Turn ${t.iteration}`),
      t.mode ? modeBadge(t.mode) : h("span", { class: "mode mode-running" }, h("span", { class: "spinner spinner-xs" }), " planning"),
      h("span", { class: "turn-who" }, who || ""),
      t.latencyMs != null && t.mode === "llm" ? h("span", { class: "turn-lat" }, duration(t.latencyMs)) : null),
    t.error && mode !== "llm" ? h("div", { class: "turn-err" }, t.error) : null,
    t.assessment ? h("div", { class: "turn-summary" }, h("span", { class: "label" }, "Planner summary (model output): "), t.assessment) : null,
    t.mode ? h("div", { class: "turn-picks" },
      t.proposed.length
        ? t.proposed.map((/** @type {any} */ p) => h("span", { class: "pick", dataset: { key: `${p.tool}:${p.entity || ""}` } }, icon("chevron", "icon-xs"), toolLabel(p.tool, p.entity)))
        : h("span", { class: "muted" }, "No further checks requested")) : null);
}

/**
 * @param {ReturnType<import("./model.js").deriveCase>} v
 * @param {string} key
 */
function stageBody(v, key) {
  if (key === "baseline") {
    const runs = v.tools.filter((t) => t.phase === "baseline");
    return runs.length ? h("div", { class: "chips" }, runs.map(toolChip)) : null;
  }
  if (key === "planner") return v.turns.length ? h("div", { class: "turns" }, v.turns.map(turnBlock),
    v.rejectedSteps.map((r) => h("div", { class: "rejected-step", dataset: { key: `rej-${r.step}-${r.ts}` } }, icon("x", "icon-xs"), `Rejected ${r.step}: ${r.reason}`))) : null;
  if (key === "follow_up") {
    const runs = v.tools.filter((t) => t.phase === "follow_up");
    return runs.length ? h("div", { class: "chips" }, runs.map(toolChip)) : null;
  }
  if (key === "policy" && v.policy) {
    return h("div", { class: "rules" }, v.policy.rules_fired.map((/** @type {any} */ r) =>
      h("div", { class: ["rule", `rule-${r.outcome.toLowerCase()}`], dataset: { key: r.rule_id } },
        h("code", null, r.rule_id), h("span", null, r.detail))));
  }
  if (key === "decision") {
    const chosen = v.decision?.decision;
    return h("div", { class: "branches" }, ["ALLOW", "REVIEW", "REJECT"].map((d) =>
      h("span", { class: ["branch", `branch-${d.toLowerCase()}`, chosen === d && "branch-on", chosen && chosen !== d && "branch-off"], dataset: { key: d } }, d)));
  }
  return null;
}

/** @param {ReturnType<import("./model.js").deriveCase>} v */
function flow(v) {
  return h("ol", { class: "flow", "aria-label": "Investigation stages" }, v.order.map((key, i) => {
    const s = v.stages[key];
    return h("li", { class: ["stage", `stage-${s.status}`, v.current === key && "stage-current"], dataset: { key } },
      h("div", { class: "stage-rail" }, stageIcon(s.status), i < v.order.length - 1 ? h("span", { class: "stage-line" }) : null),
      h("div", { class: "stage-card" },
        h("div", { class: "stage-top" },
          h("span", { class: "stage-icon" }, icon(/** @type {any} */ (/** @type {Record<string,string>} */ (STAGE_ICONS)[key]), "icon-sm")),
          h("span", { class: "stage-title" }, s.title),
          statusPill(s.status)),
        h("div", { class: "stage-desc" }, s.description),
        s.startedAt ? h("div", { class: "stage-times" },
          h("span", null, icon("clock", "icon-xs"), clock(s.startedAt)),
          s.endedAt && s.endedAt !== s.startedAt ? h("span", null, "→ ", clock(s.endedAt)) : null,
          s.durationMs != null ? h("span", { class: "stage-dur" }, duration(s.durationMs)) : null) : null,
        s.result ? h("div", { class: "stage-result" }, s.result) : null,
        stageBody(v, key)));
  }));
}

/** @param {ReturnType<import("./model.js").deriveCase>} v */
function activityFeed(v) {
  return h("section", { class: "card activity" },
    h("div", { class: "card-head" }, icon("ai", "icon-sm"), h("h3", null, "Agent activity"),
      h("span", { class: "card-note" }, "Actions and evidence summaries; no hidden model reasoning")),
    v.activity.length
      ? h("ol", { class: "feed" }, v.activity.map((a) => h("li", { class: ["feed-item", `feed-${a.tone}`], dataset: { key: a.key } },
          h("time", null, clock(a.ts)), h("span", null, a.text))))
      : h("p", { class: "muted" }, "Waiting for the first event…"));
}

/** @param {Record<string, any>} obj */
function kv(obj) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return h("span", { class: "muted" }, "none");
  return h("dl", { class: "kv" }, entries.map(([k, val]) => [h("dt", null, k), h("dd", null, typeof val === "object" ? JSON.stringify(val) : String(val))]));
}

/** @param {TrackedCase} c @param {ReturnType<import("./model.js").deriveCase>} v */
function evidence(c, v) {
  const openKey = `evidence:${c.id}`;
  return h("section", { class: "card evidence", id: "evidence" },
    h("details", { class: "evidence-details", open: state.expanded.has(openKey), dataset: { expand: openKey } },
      h("summary", null, icon("db", "icon-sm"), h("span", { class: "summary-title" }, "Investigation Evidence"),
        h("span", { class: "card-note" }, `${v.tools.length} MCP tool call${v.tools.length === 1 ? "" : "s"}`)),
      v.tools.length ? v.tools.map((t, i) => {
        const k = `tool:${c.id}:${i}`;
        const s = t.signal;
        const simulated = s ? s.simulated : null;
        return h("details", { class: "tool-ev", open: state.expanded.has(k), dataset: { expand: k, key: k } },
          h("summary", null, toolIcon(t.tool), h("code", null, t.tool + (t.entity ? `:${t.entity}` : "")),
            statusPill(t.status === "running" ? "running" : t.status),
            simulated === true ? h("span", { class: "src src-sim" }, "Simulated") : simulated === false && s?.source !== "unavailable"
              ? h("span", { class: "src src-real" }, "Real provider") : null,
            h("span", { class: "ev-time" }, clock(t.startedAt))),
          h("div", { class: "tool-ev-body" },
            h("div", { class: "ev-row" }, h("span", { class: "label" }, "Requested by"), h("span", null, t.requestedBy === "policy-baseline" ? "Policy baseline (mandatory)" : t.requestedBy === "llm" ? "AI planner (LLM)" : t.requestedBy === "deterministic_fallback" ? "Fallback rules" : "Rules planner")),
            t.rationale && t.requestedBy !== "policy-baseline" ? h("div", { class: "ev-row" }, h("span", { class: "label" }, "Reason given"), h("span", null, t.rationale)) : null,
            h("div", { class: "ev-row" }, h("span", { class: "label" }, "Sanitised request"), kv(t.args)),
            s ? h("div", { class: "ev-row" }, h("span", { class: "label" }, "Returned signal"), h("div", null,
              h("div", null, `${s.signal_type} · status ${s.status}${s.risk_score != null ? ` · risk ${s.risk_score}/100` : ""}`),
              s.reason_codes?.length ? h("div", { class: "codes" }, s.reason_codes.map((/** @type {string} */ rc) => h("code", { class: "code-chip" }, rc))) : null,
              kv(s.details))) : h("div", { class: "ev-row" }, h("span", { class: "label" }, "Returned signal"), h("span", { class: "muted" }, "waiting…")),
            s ? h("div", { class: "ev-row" }, h("span", { class: "label" }, "Source"), h("span", null, `${s.source}${simulated ? " (simulated provider, not a real third-party verification)" : ""}`)) : null,
            h("div", { class: "ev-row" }, h("span", { class: "label" }, "Timing"), h("span", null, `${clock(t.startedAt)} → ${t.endedAt ? clock(t.endedAt) : "…"}${s ? ` · ${duration(s.latency_ms)}` : ""}`)),
            s?.error ? h("div", { class: "ev-row ev-error" }, h("span", { class: "label" }, "Error"), h("span", null, s.error)) : null));
      }) : h("p", { class: "muted pad" }, "No tool has been invoked yet.")));
}

const DECISION_COPY = {
  ALLOW: "Registration approved by security policy.",
  REVIEW: "Additional verification or manual review required.",
  REJECT: "Registration rejected by security policy.",
};

/** @param {TrackedCase} c @param {ReturnType<import("./model.js").deriveCase>} v */
export function decisionCard(c, v) {
  if (v.failure) {
    return h("section", { class: "card decision decision-failed", id: "decision" },
      h("div", { class: "decision-head" }, icon("alert", "icon-lg"), h("div", null, h("div", { class: "decision-word" }, v.status === "interrupted" ? "INTERRUPTED" : "FAILED"),
        h("div", { class: "decision-msg" }, v.failure))),
      h("div", { class: "decision-meta" }, h("span", null, "Case ", h("code", null, c.id))));
  }
  const d = v.decision;
  if (!d) return null;
  const word = d.decision;
  const rules = v.policy?.rules_fired || [];
  return h("section", { class: ["card", "decision", `decision-card-${word.toLowerCase()}`], id: "decision" },
    h("div", { class: "decision-head" },
      icon(word === "ALLOW" ? "check" : word === "REVIEW" ? "alert" : "x", "icon-lg"),
      h("div", null, h("div", { class: "decision-word" }, word),
        h("div", { class: "decision-msg" }, /** @type {Record<string,string>} */ (DECISION_COPY)[word]))),
    h("dl", { class: "decision-grid" },
      h("dt", null, "Case ID"), h("dd", null, h("code", null, c.id)),
      h("dt", null, "Composite risk score"), h("dd", null, `${d.composite_score} / 100 (weighted, from the policy engine)`),
      h("dt", null, "Investigation duration"), h("dd", null, duration(d.duration_ms)),
      h("dt", null, "MCP tools invoked"), h("dd", null, String(d.tools_invoked)),
      h("dt", null, "Signals evaluated"), h("dd", null, String(d.signals_evaluated)),
      h("dt", null, "Planner"), h("dd", null, modeBadge(d.planner_mode), " ",
        d.llm_provider === "deterministic" ? "deterministic rules (no LLM)" : `${d.llm_provider}${d.llm_model ? ` · ${prettyModel(d.llm_model)}` : ""} · ${d.llm_calls} LLM call${d.llm_calls === 1 ? "" : "s"}${d.fallback_used ? " · fallback used" : ""}`),
      h("dt", null, "Policy"), h("dd", null, d.policy_version),
      h("dt", null, "Evidence"), h("dd", null, d.simulated_signals ? "Simulated providers (demo data)" : "Provider data")),
    h("div", { class: "decision-codes" }, h("span", { class: "label" }, "Policy reason codes"),
      h("div", { class: "codes" }, (d.reason_codes || []).map((/** @type {string} */ rc) => h("code", { class: "code-chip" }, rc)))),
    rules.length ? h("ul", { class: "decision-rules" }, rules.map((/** @type {any} */ r) =>
      h("li", { dataset: { key: r.rule_id } }, decisionPill(r.outcome), h("code", null, r.rule_id), h("span", null, r.detail)))) : null,
    h("p", { class: "decision-foot" }, "Decided by the deterministic policy engine. The LLM only selected which checks to run."));
}

/** @param {TrackedCase|undefined} c @param {{wide?: boolean}} [opts] */
export function orchestrationPanel(c, opts = {}) {
  if (!c) {
    return h("div", { class: "panel-empty" }, icon("flow", "icon-xl"),
      h("h3", null, "No investigation selected"),
      h("p", null, "Submit a registration, or open one from the history, to watch the security orchestration."));
  }
  const v = c.view;
  const elapsedEnd = v.decision || v.failure ? v.lastAt : new Date().toISOString();
  const head = h("div", { class: "panel-head" },
    h("div", null,
      h("div", { class: "panel-kicker" }, "Live investigation"),
      h("div", { class: "panel-case" }, h("code", null, c.id), statusPill(v.status === "completed" ? (v.decision?.decision === "ALLOW" ? "completed" : v.decision?.decision === "REVIEW" ? "review" : "rejected") : v.status))),
    h("div", { class: "panel-meta" },
      h("span", { class: ["transport", `transport-${c.transport}`] }, c.transport === "live" ? "● Live stream" : c.transport === "polling" ? "Polling" : c.transport === "connecting" ? "Connecting…" : "Recorded"),
      v.startedAt ? h("span", null, `Started ${clock(v.startedAt)}`) : null,
      v.startedAt ? h("span", null, `Elapsed ${duration(Date.parse(elapsedEnd || v.startedAt) - Date.parse(v.startedAt))}`) : null));
  const flowCard = h("section", { class: "card flow-card" }, flow(v));
  const right = [decisionCard(c, v), activityFeed(v), evidence(c, v)];
  return opts.wide
    ? h("div", { class: "panel panel-wide", dataset: { key: `panel-${c.id}` } }, head, h("div", { class: "panel-cols" }, flowCard, h("div", { class: "panel-side" }, right)))
    : h("div", { class: "panel", dataset: { key: `panel-${c.id}` } }, head, flowCard, right);
}

/**
 * @param {import("./api.js").CaseSummary[]} rows
 * @param {{compact?: boolean}} [opts]
 */
export function historyTable(rows, opts = {}) {
  if (!rows.length) return h("div", { class: "table-empty" }, "No registrations yet. Submit one above, or use a demo scenario.");
  return h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", null, h("tr", null, h("th", null, "Case ID"), h("th", null, "Email"), h("th", null, "Submitted"),
      h("th", null, "Investigation"), h("th", null, "Decision"), opts.compact ? null : h("th", null, "Planner"),
      opts.compact ? null : h("th", null, "Duration"), h("th", { class: "th-actions" }, "Actions"))),
    h("tbody", null, rows.map((r) => h("tr", { class: ["row-link", state.activeCaseId === r.case_id && "row-active"], dataset: { key: r.case_id, action: "open-case", id: r.case_id } },
      h("td", null, h("code", null, r.case_id)),
      h("td", null, r.email_masked),
      h("td", null, dateTime(r.created_at)),
      h("td", null, r.status === "running" ? h("span", { class: "pill pill-running" }, h("span", { class: "spinner spinner-xs" }), " Running") : statusPill(r.status === "completed" ? "completed" : r.status)),
      h("td", null, decisionPill(r.decision)),
      opts.compact ? null : h("td", null, modeBadge(r.planner_mode)),
      opts.compact ? null : h("td", null, duration(r.duration_ms)),
      h("td", { class: "td-actions" },
        h("button", { class: "link-btn", type: "button", dataset: { action: "view", id: r.case_id, focus: "flow" } }, "Investigation"),
        h("button", { class: "link-btn", type: "button", dataset: { action: "view", id: r.case_id, focus: "evidence" } }, "Evidence"),
        h("button", { class: "link-btn", type: "button", dataset: { action: "view", id: r.case_id, focus: "decision" } }, "Decision")))))));
}

/** Compact status strip shown when the orchestration panel is hidden (investigation still running). */
/** @param {TrackedCase|undefined} c */
export function activeStrip(c) {
  if (!c) return h("div", { class: "strip strip-idle" }, icon("shield", "icon-sm"), "No active investigation.");
  const v = c.view;
  const cur = v.current ? v.stages[v.current].title : null;
  return h("div", { class: ["strip", v.decision && `strip-${v.decision.decision.toLowerCase()}`] },
    v.decision || v.failure ? icon(v.failure ? "alert" : "check", "icon-sm") : h("span", { class: "spinner spinner-sm" }),
    h("span", null, h("code", null, c.id), " · ",
      v.decision ? `Decision ${v.decision.decision}` : v.failure ? `Failed: ${v.failure}` : `In progress: ${cur || "starting"}`),
    h("button", { class: "link-btn", type: "button", dataset: { action: "view", id: c.id, focus: "flow" } }, "Open orchestration"),
    !v.decision && !v.failure ? h("span", { class: "strip-note" }, "Panel hidden; the investigation continues in the backend.") : null);
}
