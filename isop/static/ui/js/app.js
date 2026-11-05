// @ts-check
/** Entry point: routing, page layout, header status, delegated actions. */
import { h, icon, morph } from "./dom.js";
import { buildForm, scenarioBar } from "./form.js";
import {
  changed, followSystemTheme, loadHealth, loadScenarios, refreshList, setActive, setPanel, setScenarios, setTheme,
  startBackgroundRefresh, state, subscribe, toggleExpanded, track,
} from "./store.js";
import { prettyModel } from "./model.js";
import { activeStrip, historyTable, orchestrationPanel } from "./views.js";

const NAV = [
  { route: "dashboard", label: "Dashboard", icon: "grid" },
  { route: "orchestration", label: "Security Orchestration", icon: "flow" },
  { route: "history", label: "Investigation History", icon: "history" },
];

/** @returns {{name:string, id:string|null, focus:string|null}} */
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const name = NAV.some((n) => n.route === parts[0]) ? parts[0] : "dashboard";
  return { name, id: parts[1] && /^REG-\d{8}-[0-9A-F]{8}$/.test(parts[1]) ? parts[1] : null, focus: parts[2] || null };
}

const main = /** @type {HTMLElement} */ (document.getElementById("main"));
const formApi = buildForm();
/** @type {Record<string, HTMLElement>} */
let slots = {};
let routeName = "";
/** @type {string|null} */
let pendingFocus = null;

// --------------------------------------------------------------------------- layout per route

function layoutDashboard() {
  slots = { scenarios: h("div"), panel: h("div", { class: "panel-slot" }), strip: h("div"), recent: h("div") };
  const formCard = h("section", { class: "card form-card" },
    h("div", { class: "card-head" }, icon("user", "icon-sm"), h("h2", null, "New registration"),
      h("span", { class: "card-note" }, "Submitted for AI-assisted security verification")),
    slots.scenarios, formApi.form, slots.strip);
  const grid = h("div", { class: "dash-grid", id: "dash-grid" }, h("div", { class: "dash-left" }, formCard), slots.panel);
  main.replaceChildren(
    grid,
    h("section", { class: "card" },
      h("div", { class: "card-head" }, icon("history", "icon-sm"), h("h2", null, "Recent registrations"),
        h("a", { class: "link-btn push", href: "#/history" }, "View all", icon("chevron", "icon-xs"))),
      slots.recent));
}

function layoutOrchestration() {
  slots = { picker: h("div"), page: h("div") };
  main.replaceChildren(
    h("div", { class: "page-head" }, h("div", null, h("h2", null, "Security Orchestration"),
      h("p", { class: "muted" }, "Stage-by-stage view of one investigation, rebuilt from persisted backend events.")), slots.picker),
    slots.page);
}

function layoutHistory() {
  slots = { filters: h("div"), table: h("div") };
  main.replaceChildren(
    h("div", { class: "page-head" }, h("div", null, h("h2", null, "Investigation History"),
      h("p", { class: "muted" }, "Every registration case with its investigation status and policy decision.")), slots.filters),
    h("section", { class: "card" }, slots.table));
}

// --------------------------------------------------------------------------- rendering

/** @param {HTMLElement} slot @param {Node} node */
function put(slot, node) {
  const cur = slot.firstElementChild;
  if (cur && node instanceof Element && cur.tagName === node.tagName && cur.getAttribute("data-key") === node.getAttribute("data-key")) morph(cur, node);
  else slot.replaceChildren(node);
}

/** @param {string} route */
function renderChrome(route) {
  const nav = /** @type {HTMLElement} */ (document.getElementById("nav"));
  put(nav, h("ul", null, NAV.map((n) => h("li", { dataset: { key: n.route } },
    h("a", { href: `#/${n.route}`, class: ["nav-item", route === n.route && "nav-active"], "aria-current": route === n.route ? "page" : null },
      icon(/** @type {any} */ (n.icon), "icon-sm"), h("span", null, n.label))))));

  const hl = state.health;
  const chips = /** @type {HTMLElement} */ (document.getElementById("system-chips"));
  let llmText = "Checking…", llmTone = "muted";
  if (state.healthError) { llmText = "Service unreachable"; llmTone = "bad"; }
  else if (hl) {
    const p = hl.config.llm_provider;
    if (p === "deterministic") { llmText = "Planner: deterministic rules"; llmTone = "neutral"; }
    else if (p === "ollama") {
      const ready = hl.llm.reachable && hl.llm.model_present;
      llmText = `LLM: Ollama · ${prettyModel(hl.config.model)}${ready ? "" : hl.llm.reachable ? " · model missing" : " · offline"}`;
      llmTone = ready ? "good" : "warn";
    } else { llmText = `LLM: ${p} · ${prettyModel(hl.config.model)}`; llmTone = "neutral"; }
  }
  put(chips, h("div", { class: "chips-row" },
    h("span", { class: ["chip", `chip-${llmTone}`], title: hl && hl.config.llm_provider === "ollama" && !(hl.llm.reachable && hl.llm.model_present) ? "Investigations use the labelled deterministic fallback" : "" }, h("span", { class: "chip-dot" }), llmText),
    hl ? h("span", { class: ["chip", hl.config.offline ? "chip-good" : "chip-warn"] }, hl.config.offline ? "Offline mode" : "Cloud allowed") : null,
    hl ? h("span", { class: "chip chip-neutral" }, `MCP tools: ${hl.mcp_tools.length}`) : null,
    hl ? h("span", { class: "chip chip-neutral" }, hl.policy_version) : null));

  const foot = /** @type {HTMLElement} */ (document.getElementById("sidebar-foot"));
  put(foot, h("div", null, h("div", { class: "foot-badge" }, icon("shield", "icon-xs"), "All signals simulated"),
    h("div", { class: "foot-ver" }, hl ? `ISOP ${hl.version}` : "ISOP")));

  const toggle = /** @type {HTMLInputElement} */ (document.getElementById("panel-toggle"));
  toggle.checked = state.showPanel;
  toggle.setAttribute("aria-checked", String(state.showPanel));

  const dark = state.theme === "dark";
  put(/** @type {HTMLElement} */ (document.getElementById("theme-slot")),
    h("button", { type: "button", class: "theme-btn", dataset: { action: "toggle-theme" }, "aria-pressed": String(dark),
      title: dark ? "Switch to light mode" : "Switch to dark mode" },
      icon(dark ? "sun" : "moon", "icon-sm"), h("span", null, dark ? "Light mode" : "Dark mode")));
}

function render() {
  const route = parseRoute();
  if (route.name !== routeName) {
    routeName = route.name;
    if (route.name === "dashboard") layoutDashboard();
    else if (route.name === "orchestration") layoutOrchestration();
    else layoutHistory();
  }
  renderChrome(route.name);

  if (route.name === "dashboard") {
    put(slots.scenarios, scenarioBar());
    const active = state.activeCaseId ? state.cases.get(state.activeCaseId) : undefined;
    document.getElementById("dash-grid")?.classList.toggle("with-panel", state.showPanel);
    // Hidden panel = not rendered. The tracked case keeps streaming in the store regardless.
    if (state.showPanel) put(slots.panel, orchestrationPanel(active));
    else slots.panel.replaceChildren();
    if (!state.showPanel) put(slots.strip, activeStrip(active)); else slots.strip.replaceChildren();
    put(slots.recent, historyTable(state.list.slice(0, 8), { compact: true }));
  } else if (route.name === "orchestration") {
    const id = route.id || state.activeCaseId || state.list[0]?.case_id || null;
    if (id && id !== state.activeCaseId) { setActive(id); }
    const c = id ? state.cases.get(id) : undefined;
    put(slots.picker, h("label", { class: "picker" }, h("span", null, "Case"),
      h("select", { dataset: { action: "pick-case" } },
        !id ? h("option", { value: "" }, "No cases yet") : null,
        state.list.map((r) => h("option", { value: r.case_id, selected: r.case_id === id }, `${r.case_id} · ${r.email_masked} · ${r.decision || r.status}`)))));
    put(slots.page, orchestrationPanel(c, { wide: true }));
  } else {
    const f = state.historyFilter;
    const rows = state.list.filter((r) => f === "all" || (f === "running" ? r.status === "running" : f === "failed" ? (r.status === "failed" || r.status === "interrupted") : r.decision === f));
    put(slots.filters, h("div", { class: "filters" }, ["all", "ALLOW", "REVIEW", "REJECT", "running", "failed"].map((k) =>
      h("button", { type: "button", class: ["filter", f === k && "filter-on"], dataset: { action: "filter", id: k, key: k } },
        k === "all" ? "All" : k === "running" ? "Running" : k === "failed" ? "Failed" : k,
        h("span", { class: "filter-n" }, String(k === "all" ? state.list.length : state.list.filter((r) => k === "running" ? r.status === "running" : k === "failed" ? (r.status === "failed" || r.status === "interrupted") : r.decision === k).length))))));
    put(slots.table, historyTable(rows));
  }

  if (pendingFocus) {
    const el = document.getElementById(pendingFocus);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "start" }); pendingFocus = null; }
  }
}

// --------------------------------------------------------------------------- actions (delegated)

/** @param {string} id @param {string} focus */
function openCase(id, focus) {
  if (focus === "evidence") state.expanded.add(`evidence:${id}`);
  pendingFocus = focus === "flow" ? null : focus;
  setActive(id);
  location.hash = `#/orchestration/${id}${focus && focus !== "flow" ? `/${focus}` : ""}`;
}

document.addEventListener("click", (ev) => {
  const target = /** @type {HTMLElement} */ (ev.target);
  const summary = target.closest("summary");
  const details = summary?.parentElement;
  if (summary && details instanceof HTMLDetailsElement && details.dataset.expand) {
    ev.preventDefault(); // open state lives in the store so live re-renders never collapse it
    toggleExpanded(details.dataset.expand);
    return;
  }
  const el = /** @type {HTMLElement|null} */ (target.closest("[data-action]"));
  if (!el) return;
  const { action, id, focus } = el.dataset;
  if (action === "prefill") {
    const s = state.scenarios.find((x) => x.id === id);
    if (s) formApi.fill(s.payload);
    document.getElementById("f-first_name")?.focus();
  } else if (action === "toggle-scenarios") setScenarios(!state.showScenarios);
  else if (action === "toggle-theme") setTheme(state.theme === "dark" ? "light" : "dark");
  else if (action === "clear-form") formApi.clear();
  else if (action === "view" && id) { ev.stopPropagation(); openCase(id, focus || "flow"); }
  else if (action === "open-case" && id) openCase(id, "flow");
  else if (action === "filter" && id) { state.historyFilter = id; changed(); }
});

document.addEventListener("change", (ev) => {
  const el = /** @type {HTMLSelectElement} */ (ev.target);
  if (el.dataset?.action === "pick-case" && el.value) openCase(el.value, "flow");
});

const toggle = /** @type {HTMLInputElement} */ (document.getElementById("panel-toggle"));
toggle.addEventListener("change", () => setPanel(toggle.checked));

window.addEventListener("hashchange", () => {
  const r = parseRoute();
  if (r.focus) pendingFocus = r.focus;
  if (r.id) { if (r.focus === "evidence") state.expanded.add(`evidence:${r.id}`); setActive(r.id); }
  render();
});

// Ticks the elapsed-time readout while something is running (display only; no requests).
setInterval(() => { for (const c of state.cases.values()) if (!c.view.decision && !c.view.failure && c.events.length) { changed(); break; } }, 1000);

subscribe(render);
followSystemTheme();

(async function boot() {
  const r = parseRoute();
  if (r.focus) pendingFocus = r.focus;
  if (r.id && r.focus === "evidence") state.expanded.add(`evidence:${r.id}`);
  render();
  await Promise.all([loadHealth(), loadScenarios(), refreshList()]);
  // After a refresh, resume following any case still running so its timeline keeps updating.
  for (const row of state.list) if (row.status === "running") track(row.case_id);
  if (r.id) setActive(r.id);
  else if (!state.activeCaseId && state.list[0]) setActive(state.list[0].case_id);
  startBackgroundRefresh();
})();
