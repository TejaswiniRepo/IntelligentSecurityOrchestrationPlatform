// @ts-check
/**
 * Registration form. Built once and managed imperatively (never re-rendered), so typing is never
 * disturbed by live investigation updates. Validation mirrors the backend; the backend re-validates.
 */
import { api, ApiError } from "./api.js";
import { h, icon, toast } from "./dom.js";
import { setActive, refreshList, state } from "./store.js";

const COUNTRIES = [["", "Not specified"], ["US", "United States (US)"], ["GB", "United Kingdom (GB)"], ["IN", "India (IN)"],
  ["DE", "Germany (DE)"], ["AU", "Australia (AU)"], ["CA", "Canada (CA)"], ["FR", "France (FR)"], ["SG", "Singapore (SG)"],
  ["AE", "United Arab Emirates (AE)"]];

const NAME = /^[\p{L}][\p{L} .'-]{0,49}$/u;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const E164 = /^\+[1-9]\d{7,14}$/;
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const IPV6 = /^[0-9a-fA-F:]{2,39}$/;
const DEVICE = /^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$/;
const FINGERPRINT = /^[A-Za-z0-9:_-]{8,128}$/;

/**
 * @typedef {{name:string, label:string, required:boolean, type?:string, placeholder?:string, hint?:string,
 *   validate:(v:string)=>string|null, autocomplete?:string, options?:string[][]}} FieldDef
 * @type {FieldDef[]}
 */
const FIELDS = [
  { name: "first_name", label: "First Name", required: true, placeholder: "Alice", autocomplete: "off",
    validate: (v) => (NAME.test(v) ? null : "Use letters, spaces, hyphens or apostrophes (max 50).") },
  { name: "last_name", label: "Last Name", required: true, placeholder: "Walker", autocomplete: "off",
    validate: (v) => (NAME.test(v) ? null : "Use letters, spaces, hyphens or apostrophes (max 50).") },
  { name: "email", label: "Email Address", required: true, type: "email", placeholder: "alice.walker@gmail.com", autocomplete: "off",
    validate: (v) => (EMAIL.test(v) ? null : "Enter a valid email address, e.g. name@example.com.") },
  { name: "phone", label: "Mobile Number", required: true, type: "tel", placeholder: "+14155550123", hint: "International E.164 format",
    validate: (v) => (E164.test(v) ? null : "Use E.164 format: + then country code and number, e.g. +14155550123.") },
  { name: "ip_address", label: "IP Address", required: true, placeholder: "192.0.2.10", hint: "As observed by the registration service",
    validate: (v) => (IPV4.test(v) || (v.includes(":") && IPV6.test(v)) ? null : "Enter a valid IPv4 or IPv6 address.") },
  { name: "device_id", label: "Device ID", required: true, placeholder: "dev-a1b2c3",
    validate: (v) => (DEVICE.test(v) ? null : "3–64 characters: letters, digits, _ or -, starting with a letter or digit.") },
  { name: "device_fingerprint", label: "Device Fingerprint", required: false, placeholder: "fp-3c9a71d2e8b4", hint: "Optional · recorded as a hash only",
    validate: (v) => (!v || FINGERPRINT.test(v) ? null : "8–128 characters: letters, digits, :, _ or -.") },
  { name: "country", label: "Country / Location", required: false, options: COUNTRIES, hint: "Optional · enables the phone–country check",
    validate: (v) => (!v || /^[A-Z]{2}$/.test(v) ? null : "Choose a country.") },
];

function newKey() {
  if (crypto && "randomUUID" in crypto) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function buildForm() {
  /** @type {Record<string, HTMLInputElement|HTMLSelectElement>} */
  const inputs = {};
  /** @type {Record<string, HTMLElement>} */
  const errors = {};
  let busy = false;
  /** Reuse the key when an identical payload is retried after a lost response, so no duplicate case is made. */
  let last = { body: "", key: "", ok: false };

  const status = h("div", { class: "form-status", role: "status", "aria-live": "polite" });
  const submitLabel = h("span", null, "Register & Verify Security");
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, icon("shield", "icon-sm"), submitLabel);

  const fieldEls = FIELDS.map((f) => {
    const id = `f-${f.name}`;
    const control = f.options
      ? h("select", { id, name: f.name }, f.options.map(([v, t]) => h("option", { value: v }, t)))
      : h("input", { id, name: f.name, type: f.type || "text", placeholder: f.placeholder || "", autocomplete: f.autocomplete || "off",
          spellcheck: "false", required: f.required, "aria-describedby": `${id}-err` });
    inputs[f.name] = /** @type {any} */ (control);
    errors[f.name] = h("div", { class: "field-error", id: `${id}-err` });
    control.addEventListener("input", () => { if (errors[f.name].textContent) check(f); });
    control.addEventListener("blur", () => check(f));
    return h("div", { class: ["field", f.name === "email" && "field-wide"] },
      h("label", { for: id }, f.label, f.required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : h("span", { class: "opt" }, " optional")),
      control, f.hint ? h("div", { class: "field-hint" }, f.hint) : null, errors[f.name]);
  });

  /** @param {FieldDef} f */
  function check(f) {
    const v = inputs[f.name].value.trim();
    const msg = !v ? (f.required ? `${f.label} is required.` : null) : f.validate(v);
    errors[f.name].textContent = msg || "";
    inputs[f.name].classList.toggle("invalid", !!msg);
    inputs[f.name].setAttribute("aria-invalid", msg ? "true" : "false");
    return !msg;
  }

  function payload() {
    /** @type {Record<string,string>} */
    const p = {};
    for (const f of FIELDS) { const v = inputs[f.name].value.trim(); if (v) p[f.name] = v; }
    return /** @type {import("./api.js").RegistrationPayload} */ (/** @type {unknown} */ (p));
  }

  /** @param {string} text @param {"info"|"success"|"error"} tone @param {string} [caseId] */
  function setStatus(text, tone, caseId) {
    status.replaceChildren(h("div", { class: ["alert", `alert-${tone}`] },
      icon(tone === "success" ? "check" : tone === "error" ? "alert" : "clock", "icon-sm"), h("span", null, text),
      caseId ? h("button", { type: "button", class: "link-btn", dataset: { action: "view", id: caseId, focus: "flow" } }, "View orchestration") : null));
  }

  const form = h("form", { class: "reg-form", novalidate: true, autocomplete: "off" },
    h("div", { class: "fields" }, fieldEls),
    h("div", { class: "form-actions" }, submit,
      h("button", { class: "btn btn-ghost", type: "button", dataset: { action: "clear-form" } }, "Clear"),
      h("span", { class: "form-note" }, "No password is requested. Names are validated but not sent to any risk check.")),
    status);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (busy) return; // the button is also disabled; this guards against keyboard double-submits
    const okAll = FIELDS.map(check).every(Boolean);
    if (!okAll) { setStatus("Please correct the highlighted fields.", "error"); form.querySelector(".invalid")?.focus?.(); return; }
    const body = payload();
    const json = JSON.stringify(body);
    const key = json === last.body && !last.ok ? last.key : newKey();
    last = { body: json, key, ok: false };
    busy = true;
    submit.setAttribute("disabled", "");
    submitLabel.textContent = "Submitting…";
    submit.prepend(h("span", { class: "spinner spinner-sm btn-spinner" }));
    setStatus("Submitting registration for security verification…", "info");
    try {
      const res = await api.submit(body, key);
      last.ok = true;
      setActive(res.case_id);
      refreshList();
      setStatus(res.created ? `Case ${res.case_id} created. Security investigation started in the backend.`
        : `Duplicate submission detected: showing existing case ${res.case_id}.`, "success", res.case_id);
      toast(`Investigation started: ${res.case_id}`, "success");
    } catch (e) {
      const err = /** @type {ApiError} */ (e);
      if (err.status === 422 && Array.isArray(err.detail)) {
        for (const d of err.detail) {
          const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : null;
          if (field && errors[field]) { errors[field].textContent = d.msg; inputs[field].classList.add("invalid"); }
        }
        setStatus("The server rejected some fields. Please correct them and try again.", "error");
      } else {
        setStatus(err.message || "Submission failed.", "error");
      }
    } finally {
      busy = false;
      submit.removeAttribute("disabled");
      submit.querySelector(".btn-spinner")?.remove();
      submitLabel.textContent = "Register & Verify Security";
    }
  });

  /** @param {import("./api.js").RegistrationPayload} p */
  function fill(p) {
    for (const f of FIELDS) {
      inputs[f.name].value = /** @type {Record<string,string>} */ (/** @type {unknown} */ (p))[f.name] || "";
      errors[f.name].textContent = "";
      inputs[f.name].classList.remove("invalid");
    }
    status.replaceChildren();
  }

  function clear() { fill(/** @type {any} */ ({})); }

  return { form, fill, clear };
}

/** Demo scenario picker (collapsible): prefills the form; the user still clicks submit. */
export function scenarioBar() {
  const open = state.showScenarios;
  return h("div", { class: ["scenarios", open ? "scenarios-open" : "scenarios-closed"] },
    h("button", { type: "button", class: "scenarios-toggle", "aria-expanded": String(open), "aria-controls": "scenario-list",
      dataset: { action: "toggle-scenarios" } },
      icon("play", "icon-sm"), h("span", null, "Demo scenarios"),
      h("span", { class: "card-note" }, open ? "Prefill the form · all evidence is simulated"
        : `${state.scenarios.length} prefilled registrations available`),
      h("span", { class: "chev" }, open ? "Hide" : "Show", icon("chevron", "icon-xs"))),
    !open ? null : h("div", { class: "scenario-list", id: "scenario-list" }, state.scenarios.map((s) =>
      h("button", { type: "button", class: ["scenario", `scenario-${s.expected.toLowerCase()}`], dataset: { action: "prefill", id: s.id, key: s.id }, title: s.summary },
        h("span", { class: "scenario-title" }, s.title),
        h("span", { class: ["decision-pill", `decision-${s.expected.toLowerCase()}`] }, `expect ${s.expected}`),
        h("span", { class: "scenario-sum" }, s.summary)))));
}
