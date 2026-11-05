// @ts-check
/**
 * Tiny DOM toolkit. All dynamic text is set with textContent (never innerHTML), so backend data
 * cannot inject markup. Interactive elements use `data-action` + one delegated listener, which keeps
 * behaviour intact when `morph` reuses existing nodes.
 */

/**
 * @typedef {string | number | null | undefined | false | Node | Child[]} Child
 * @param {string} tag
 * @param {Record<string, any> | null} [attrs]
 * @param {...Child} children
 * @returns {HTMLElement}
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

/** @param {Element} el @param {Record<string, any> | null | undefined} attrs */
function setAttrs(el, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", Array.isArray(v) ? v.filter(Boolean).join(" ") : String(v));
    else if (k === "dataset") for (const [dk, dv] of Object.entries(v)) { if (dv != null) el.setAttribute(`data-${dk}`, String(dv)); }
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

/** @param {Node} el @param {Child[]} children */
function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** Stroke icons drawn on a 24x24 grid (no external assets: works offline). */
const ICONS = {
  shield: ["M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z", "M9 12l2 2 4-4"],
  grid: ["M4 4h7v7H4z", "M13 4h7v7h-7z", "M4 13h7v7H4z", "M13 13h7v7h-7z"],
  flow: ["M6 4v4", "M6 16v4", "M18 10v4", "M6 8a2 2 0 100 4 2 2 0 000-4z", "M18 6a2 2 0 100 4 2 2 0 000-4z", "M18 14a2 2 0 100 4 2 2 0 000-4z", "M8 10h4l4-2", "M8 10h4l4 6"],
  history: ["M3 12a9 9 0 103-6.7", "M3 4v4h4", "M12 7v5l3 3"],
  check: ["M5 12l5 5 9-10"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  alert: ["M12 3l10 18H2L12 3z", "M12 10v5", "M12 18v.5"],
  clock: ["M12 3a9 9 0 110 18 9 9 0 010-18z", "M12 7v5l3 2"],
  mail: ["M3 6h18v12H3z", "M3 7l9 6 9-6"],
  phone: ["M8 3h8v18H8z", "M11 18h2"],
  globe: ["M12 3a9 9 0 110 18 9 9 0 010-18z", "M3 12h18", "M12 3c3 3 3 15 0 18", "M12 3c-3 3-3 15 0 18"],
  device: ["M4 5h16v11H4z", "M9 20h6", "M12 16v4"],
  history2: ["M4 6h16", "M4 12h10", "M4 18h7", "M17 15l2 2 3-4"],
  ai: ["M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8L12 3z", "M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9L18 15z"],
  scale: ["M12 4v16", "M6 20h12", "M5 8h14", "M5 8l-3 6h6l-3-6z", "M19 8l-3 6h6l-3-6z"],
  layers: ["M12 3l9 5-9 5-9-5 9-5z", "M3 13l9 5 9-5"],
  inbox: ["M4 4h16v16H4z", "M4 13h5l1 3h4l1-3h5"],
  db: ["M12 4c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3z", "M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7", "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"],
  chevron: ["M9 6l6 6-6 6"],
  play: ["M7 5l12 7-12 7V5z"],
  user: ["M12 4a4 4 0 110 8 4 4 0 010-8z", "M4 21c1-4 4.5-6 8-6s7 2 8 6"],
  dot: ["M12 11a1 1 0 100 2 1 1 0 000-2z"],
  external: ["M14 4h6v6", "M20 4l-9 9", "M18 14v6H4V6h6"],
  sun: ["M12 8a4 4 0 100 8 4 4 0 000-8z", "M12 2v2", "M12 20v2", "M4.9 4.9l1.4 1.4", "M17.7 17.7l1.4 1.4", "M2 12h2", "M20 12h2", "M4.9 19.1l1.4-1.4", "M17.7 6.3l1.4-1.4"],
  moon: ["M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"],
};

/** @param {keyof typeof ICONS} name @param {string} [cls] */
export function icon(name, cls = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  for (const d of ICONS[name] || ICONS.dot) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
  }
  return svg;
}

/**
 * Patch `from` in place to look like `to`, reusing nodes so CSS transitions run on state changes and
 * entry animations play only for genuinely new elements. Children are matched by `data-key` when present.
 * @param {Element} from @param {Element} to
 */
export function morph(from, to) {
  if (from.tagName !== to.tagName) { from.replaceWith(to); return; }
  syncAttributes(from, to);
  morphChildren(from, to);
}

/** @param {Element} from @param {Element} to */
function syncAttributes(from, to) {
  for (const { name } of Array.from(from.attributes)) if (!to.hasAttribute(name)) from.removeAttribute(name);
  for (const { name, value } of Array.from(to.attributes)) if (from.getAttribute(name) !== value) from.setAttribute(name, value);
}

/** @param {Node} n */
const keyOf = (n) => (n instanceof Element ? n.getAttribute("data-key") : null);

/** @param {Element} from @param {Element} to */
function morphChildren(from, to) {
  const oldKids = Array.from(from.childNodes);
  const keyed = new Map();
  for (const k of oldKids) { const key = keyOf(k); if (key) keyed.set(key, k); }
  const used = new Set();
  let unkeyedIdx = 0;
  const unkeyed = oldKids.filter((k) => !keyOf(k));
  const result = [];
  for (const nk of Array.from(to.childNodes)) {
    const key = keyOf(nk);
    let match = key ? keyed.get(key) : undefined;
    if (!key) {
      while (unkeyedIdx < unkeyed.length && used.has(unkeyed[unkeyedIdx])) unkeyedIdx++;
      const cand = unkeyed[unkeyedIdx];
      if (cand && cand.nodeType === nk.nodeType && (!(cand instanceof Element) || (nk instanceof Element && cand.tagName === nk.tagName))) {
        match = cand; unkeyedIdx++;
      }
    }
    if (match && !used.has(match)) {
      used.add(match);
      if (match.nodeType === Node.TEXT_NODE) { if (match.nodeValue !== nk.nodeValue) match.nodeValue = nk.nodeValue; }
      else if (match instanceof Element && nk instanceof Element) {
        if (match.tagName === nk.tagName) morph(match, nk); else { result.push(nk); continue; }
      }
      result.push(match);
    } else {
      result.push(nk);
    }
  }
  for (const k of oldKids) if (!used.has(k)) k.remove();
  result.forEach((node, i) => {
    const at = from.childNodes[i];
    if (at !== node) from.insertBefore(node, at || null);
  });
}

/** @param {string} iso */
export function clock(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

/** @param {number | null | undefined} ms */
export function duration(ms) {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}

/** @param {string} iso */
export function dateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString([], { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString([], { hour12: false });
}

/** @param {string} text @param {"info"|"success"|"error"} [tone] */
export function toast(text, tone = "info") {
  const host = document.getElementById("toasts");
  if (!host) return;
  const t = h("div", { class: ["toast", `toast-${tone}`], role: "status" }, text);
  host.appendChild(t);
  setTimeout(() => t.classList.add("toast-out"), 4200);
  setTimeout(() => t.remove(), 4800);
}
