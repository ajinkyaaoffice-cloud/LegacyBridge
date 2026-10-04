// Page transitions: a shutter of punch cards in the destination's colour rises over the page,
// the jump happens underneath (a scroll, a station switch, or a new page), then the cards lift off.
// Cross-page: the cover state is handed over in sessionStorage and the next page starts covered
// (see the inline <script> in each page's <head>), then uncovers once it has booted.

import { reducedMotion } from "./api.js";

const KEY = "lb.tx";
const COLS = 7;
let el = null, busy = false;

const INK = { "#E5FFC3": "#013330", "#013330": "#E5FFC3", "#190066": "#CEE9FD", "#CEE9FD": "#190066", "#FFA680": "#661800",
  "#FFFFFF": "#013330", "#EAC2FF": "#3D065F", "#BAF24A": "#013330", "#0B2B28": "#E5FFC3" };

function build() {
  if (el) return el;
  el = document.getElementById("tx") || document.createElement("div");
  el.id = "tx";
  el.className = el.className || "tx";
  el.setAttribute("aria-hidden", "true");
  if (!el.children.length) {
    for (let i = 0; i < COLS; i++) {
      const c = document.createElement("i");
      c.style.setProperty("--i", i);
      el.append(c);
    }
    const label = document.createElement("b");
    label.className = "tx-label";
    el.append(label);
  }
  if (!el.isConnected) document.body.append(el);
  return el;
}

function paintColors(color, label) {
  const t = build();
  t.style.setProperty("--tx-bg", color);
  t.style.setProperty("--tx-fg", INK[color.toUpperCase()] || "#013330");
  t.querySelector(".tx-label").textContent = label || "";
}

const cols = () => [...build().querySelectorAll("i")];
// rAF (so gsap) pauses in hidden tabs: never wait on a tween longer than it could possibly take
const settle = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(r, ms))]);
const order = (fromRight) => (fromRight ? cols().reverse() : cols());

function cover({ color, label, fromRight }) {
  paintColors(color, label);
  const t = build();
  t.classList.add("is-on");
  const g = window.gsap;
  if (!g || reducedMotion()) { t.classList.add("is-covered"); return Promise.resolve(); }
  return new Promise((done) => {
    g.killTweensOf([...cols(), t.querySelector(".tx-label")]);
    g.set(cols(), { yPercent: 105 });
    g.set(t.querySelector(".tx-label"), { yPercent: 120, opacity: 0 });
    g.timeline({ onComplete: done })
      .to(order(fromRight), { yPercent: 0, duration: .55, ease: "expo.inOut", stagger: .045 }, 0)
      .to(t.querySelector(".tx-label"), { yPercent: 0, opacity: 1, duration: .45, ease: "expo.out" }, .3);
  });
}

function uncover({ fromRight } = {}) {
  const t = build();
  const g = window.gsap;
  t.classList.remove("is-covered");
  if (!g || reducedMotion()) { t.classList.remove("is-on"); return Promise.resolve(); }
  return new Promise((done) => {
    g.timeline({ onComplete: () => { t.classList.remove("is-on"); done(); } })
      .to(t.querySelector(".tx-label"), { yPercent: -120, opacity: 0, duration: .35, ease: "power2.in" }, 0)
      .fromTo(order(fromRight), { yPercent: 0 }, { yPercent: -105, duration: .65, ease: "expo.inOut", stagger: .045 }, .12);
  });
}

/**
 * Run `jump` (sync or async) under a cover. Options: color (destination background), label,
 * x (pointer x, so the shutter starts on the side you clicked).
 */
export async function transition(jump, { color = "#013330", label = "", x = null } = {}) {
  if (busy) return;
  if (document.hidden) { try { await jump(); } catch (e) { console.error(e); } return; }
  busy = true;
  const fromRight = x != null && x > window.innerWidth / 2;
  document.body.classList.add("is-transitioning");
  try {
    await settle(cover({ color, label, fromRight }), 1600);
    try { await jump(); } catch (e) { console.error(e); }
    await settle(new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))), 120);
    if (!(await settle(uncover({ fromRight }).then(() => true), 1800))) reset();
  } catch (e) {
    console.error(e);
    reset();
  } finally {
    busy = false;
    document.body.classList.remove("is-transitioning");
  }
}

/** Cover, then load another page; that page uncovers itself with `arrive()`. */
export async function leave(href, { color = "#013330", label = "", x = null } = {}) {
  if (busy) return;
  busy = true;
  const fromRight = x != null && x > window.innerWidth / 2;
  if (!document.hidden) await settle(cover({ color, label, fromRight }), 1600);
  try { sessionStorage.setItem(KEY, JSON.stringify({ color, label, fromRight, t: Date.now() })); } catch { /* private mode */ }
  location.href = href;
  // if the navigation never happens (or is cancelled), don't leave the page covered
  setTimeout(() => { if (document.visibilityState === "visible") { try { sessionStorage.removeItem(KEY); } catch { /* */ } uncover({ fromRight }).then(() => { busy = false; }); } }, 6000);
}

/** Drop every trace of a cover, instantly (error paths, bfcache restores, the boot watchdog). */
export function reset() {
  const t = document.getElementById("tx");
  document.documentElement.classList.remove("tx-pre");
  document.body.classList.remove("is-transitioning");
  if (!t) return;
  window.gsap?.killTweensOf([...t.children]);
  window.gsap?.set(t.querySelectorAll("i"), { yPercent: 105 });
  window.gsap?.set(t.querySelector(".tx-label"), { opacity: 0 });
  t.classList.remove("is-on", "is-covered");
  busy = false;
}

// coming back through the back/forward cache restores the page exactly as it was left: covered
window.addEventListener("pageshow", (e) => { if (e.persisted) { try { sessionStorage.removeItem(KEY); } catch { /* */ } reset(); } });

/** Did we arrive through a transition? (read once, by the page's boot) */
export function arriving() {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
    const v = JSON.parse(raw);
    return Date.now() - v.t < 15000 ? v : null;
  } catch { return null; }
}

/** Uncover a page that started covered. */
export function arrive(state) {
  paintColors(state.color, state.label);
  build().classList.add("is-on");
  document.documentElement.classList.remove("tx-pre");
  if (document.hidden) { reset(); return Promise.resolve(); }
  return settle(uncover({ fromRight: state.fromRight }), 1800).then(() => { if (build().classList.contains("is-on") && !busy) reset(); });
}

export const isBusy = () => busy;
