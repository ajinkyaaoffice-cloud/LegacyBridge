// Tiny API client, shared state and an event bus. Everything on the page reads `store`.

export const store = {
  session: null,        // {user, live_providers, cobc, cobc_note, specimen}
  runs: [],             // the signed-in user's runs
  run: null,            // the run the page currently shows (specimen or one of yours)
  snapshot: null,       // /state for that run
  selected: null,       // selected unit name
  units: new Map(),     // unit detail cache for the current run
  replaying: false,
};

export const bus = new EventTarget();
export const emit = (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail }));
export const on = (type, fn) => bus.addEventListener(type, (e) => fn(e.detail));

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && data.detail) || res.statusText || "Request failed";
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

export function toast(msg, { tag = "note", bad = false, ms = 4200 } = {}) {
  const box = $("#toasts");
  const t = h("div", { class: "toast" + (bad ? " is-bad" : "") }, h("b", { text: tag }), h("span", { text: msg }));
  box.append(t);
  setTimeout(() => { t.classList.add("is-out"); setTimeout(() => t.remove(), 400); }, ms);
}

export const STAGE_PROGRESS = {
  pending: 0, oracle: .14, ready: .22, translating: .36, verifying: .5, repairing: .55, fuzzing: .78,
  verified: 1, blocked: 1, dep_blocked: 1,
};

export const fmtAgo = (ts) => {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
