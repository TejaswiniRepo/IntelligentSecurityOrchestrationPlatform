// @ts-check
/**
 * Client state. Each case is tracked by its case ID; its events are fetched once (so a refresh rebuilds
 * the timeline), then streamed with SSE and resumed from the last event ID. If the stream keeps failing,
 * the store falls back to polling every POLL_MS until the case finishes. Visibility of the orchestration
 * panel is a separate flag: hiding it never closes a stream or touches the backend investigation.
 */
import { api } from "./api.js";
import { deriveCase } from "./model.js";

export const POLL_MS = 1000;
const LIST_REFRESH_MS = 4000;
const MAX_SSE_ERRORS = 3;

/**
 * @typedef {import("./api.js").CaseEvent} CaseEvent
 * @typedef {import("./api.js").CaseSummary} CaseSummary
 * @typedef {Object} TrackedCase
 * @property {string} id
 * @property {CaseEvent[]} events
 * @property {Set<number>} seen
 * @property {number} lastId
 * @property {import("./api.js").CaseStatus} status
 * @property {"connecting"|"live"|"polling"|"closed"} transport
 * @property {EventSource|null} source
 * @property {number|null} pollTimer
 * @property {number} sseErrors
 * @property {ReturnType<typeof deriveCase>} view
 */

const PANEL_KEY = "isop.showOrchestration";
const SCENARIOS_KEY = "isop.showScenarios";
const THEME_KEY = "isop.theme";

/** @param {string} key @param {boolean} fallback */
function readFlag(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : v !== "0"; } catch { return fallback; }
}
/** @param {string} key @param {string} value */
function save(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode: preference lasts for this page only */ }
}
function readPanelPref() { return readFlag(PANEL_KEY, true); }

export const state = {
  /** @type {import("./api.js").Health|null} */ health: null,
  /** @type {string|null} */ healthError: null,
  /** @type {import("./api.js").Scenario[]} */ scenarios: [],
  /** @type {CaseSummary[]} */ list: [],
  /** @type {Map<string, TrackedCase>} */ cases: new Map(),
  /** @type {string|null} */ activeCaseId: null,
  showPanel: readPanelPref(),
  showScenarios: readFlag(SCENARIOS_KEY, true),
  /** @type {"light"|"dark"} */
  theme: document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light",
  /** @type {Set<string>} */ expanded: new Set(),
  historyFilter: "all",
};

/** @type {Set<() => void>} */
const listeners = new Set();
let scheduled = false;

/** @param {() => void} fn */
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export function changed() {
  if (scheduled) return;
  scheduled = true;
  // A timer, not requestAnimationFrame: rAF is paused in background tabs, which would freeze the view.
  setTimeout(() => { scheduled = false; for (const fn of listeners) fn(); }, 16);
}

/** @param {boolean} on */
export function setPanel(on) {
  state.showPanel = on; // visibility only: streams and investigations are untouched
  save(PANEL_KEY, on ? "1" : "0");
  changed();
}

/** @param {boolean} open */
export function setScenarios(open) {
  state.showScenarios = open; // presentation only; the form keeps whatever was typed
  save(SCENARIOS_KEY, open ? "1" : "0");
  changed();
}

/** @param {"light"|"dark"} theme @param {boolean} [remember] */
export function setTheme(theme, remember = true) {
  state.theme = theme;
  document.documentElement.setAttribute("data-theme", theme);
  if (remember) save(THEME_KEY, theme);
  changed();
}

/** Follow OS light/dark changes until the user picks a theme explicitly. */
export function followSystemTheme() {
  if (!window.matchMedia) return;
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch { /* ignore */ }
    if (!saved) setTheme(e.matches ? "dark" : "light", false);
  });
}

/** @param {string|null} id */
export function setActive(id) {
  state.activeCaseId = id;
  if (id) track(id);
  changed();
}

/** @param {string} key */
export function toggleExpanded(key) {
  if (state.expanded.has(key)) state.expanded.delete(key); else state.expanded.add(key);
  changed();
}

/** @param {TrackedCase} c @param {CaseEvent[]} evs */
function ingest(c, evs) {
  let added = false;
  for (const e of evs) {
    if (c.seen.has(e.event_id)) continue; // de-duplicate across fetch, stream and poll
    c.seen.add(e.event_id);
    c.events.push(e);
    c.lastId = Math.max(c.lastId, e.event_id);
    added = true;
    if (e.type === "case.completed") c.status = "completed";
    if (e.type === "case.failed" && c.status === "running") c.status = "failed";
  }
  if (added) {
    c.events.sort((a, b) => a.event_id - b.event_id);
    c.view = deriveCase(c.events, c.status);
    patchSummary(c);
    changed();
  }
}

/** Keep the history row in step with live events without waiting for the next list refresh. */
/** @param {TrackedCase} c */
function patchSummary(c) {
  const row = state.list.find((r) => r.case_id === c.id);
  if (!row) return;
  row.status = c.view.status === "interrupted" ? "interrupted" : c.view.decision ? "completed" : c.view.failure ? "failed" : "running";
  if (c.view.decision) {
    row.decision = c.view.decision.decision;
    row.planner_mode = c.view.decision.planner_mode;
    row.duration_ms = c.view.decision.duration_ms;
  }
}

const isTerminal = (/** @type {string} */ s) => s === "completed" || s === "failed" || s === "interrupted";

/** Start following a case (idempotent: a case is only ever subscribed once). */
/** @param {string} id */
export async function track(id) {
  if (state.cases.has(id)) return state.cases.get(id);
  /** @type {TrackedCase} */
  const c = { id, events: [], seen: new Set(), lastId: 0, status: "running", transport: "connecting", source: null,
    pollTimer: null, sseErrors: 0, view: deriveCase([], "running") };
  state.cases.set(id, c);
  try {
    const snap = await api.events(id, 0);
    c.status = snap.status;
    ingest(c, snap.events);
    c.view = deriveCase(c.events, c.status);
  } catch {
    c.transport = "closed";
    changed();
    return c;
  }
  if (isTerminal(c.status)) { c.transport = "closed"; changed(); return c; }
  openStream(c);
  return c;
}

/** @param {TrackedCase} c */
function openStream(c) {
  if (typeof EventSource === "undefined") { startPolling(c); return; }
  const es = new EventSource(api.streamUrl(c.id, c.lastId));
  c.source = es;
  c.transport = "connecting";
  es.addEventListener("open", () => { c.transport = "live"; c.sseErrors = 0; changed(); });
  es.addEventListener("progress", (msg) => {
    try { ingest(c, [JSON.parse(/** @type {MessageEvent} */ (msg).data)]); } catch { /* ignore a malformed frame */ }
  });
  es.addEventListener("end", () => { closeStream(c); finish(c); });
  es.addEventListener("error", () => {
    // EventSource reconnects by itself and sends Last-Event-ID, so the backend resumes without duplicates.
    c.sseErrors += 1;
    c.transport = "connecting";
    if (c.sseErrors >= MAX_SSE_ERRORS) { closeStream(c); startPolling(c); }
    changed();
  });
}

/** @param {TrackedCase} c */
function closeStream(c) {
  if (c.source) { c.source.close(); c.source = null; }
  c.transport = "closed";
}

/** @param {TrackedCase} c */
function startPolling(c) {
  c.transport = "polling";
  const tick = async () => {
    try {
      const snap = await api.events(c.id, c.lastId);
      ingest(c, snap.events);
      c.status = snap.status;
      if (isTerminal(snap.status)) { finish(c); return; }
    } catch { /* keep polling: the server may be restarting */ }
    c.pollTimer = window.setTimeout(tick, POLL_MS);
  };
  tick();
  changed();
}

/** @param {TrackedCase} c */
async function finish(c) {
  if (c.pollTimer) { clearTimeout(c.pollTimer); c.pollTimer = null; }
  c.transport = "closed";
  try { // fetch any event that raced the end of the stream, then refresh the summary row
    const snap = await api.events(c.id, c.lastId);
    c.status = snap.status;
    ingest(c, snap.events);
    c.view = deriveCase(c.events, c.status);
  } catch { /* the view already holds what was streamed */ }
  refreshList();
  changed();
}

export async function refreshList() {
  try {
    state.list = await api.list(50);
    for (const row of state.list) { // fold in live state for tracked cases
      const c = state.cases.get(row.case_id);
      if (c) patchSummary(c);
    }
  } catch { /* table keeps its last rows */ }
  changed();
}

export async function loadHealth() {
  try { state.health = await api.health(); state.healthError = null; }
  catch (e) { state.healthError = /** @type {Error} */ (e).message; }
  changed();
}

export async function loadScenarios() {
  try { state.scenarios = await api.scenarios(); } catch { state.scenarios = []; }
  changed();
}

let listTimer = 0;
export function startBackgroundRefresh() {
  if (listTimer) return;
  listTimer = window.setInterval(() => {
    if (state.list.some((r) => r.status === "running")) refreshList();
  }, LIST_REFRESH_MS);
  window.setInterval(loadHealth, 30000);
}
