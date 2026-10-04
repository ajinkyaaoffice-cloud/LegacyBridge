// What both pages share: the nav (every link plays a transition, even to a section on the same
// page), the mega menu, the command palette, Punchy, the cursor and the toasts.

import { $, $$, api, on, reducedMotion, store } from "./api.js";
import { initCursor } from "./cursor.js";
import { createKeystone } from "./keystone.js";
import { leave, transition } from "./transition.js";

const PAGE = document.body.dataset.page;      // "story" | "machine"

// where a link goes: page path + hash, the colour of the destination and the word on the shutter
const DEST = {
  "/#top": ["#E5FFC3", "Proof"], "/#how": ["#E5FFC3", "How"], "/#wiring": ["#190066", "Wiring"],
  "/#sandbox": ["#FFA680", "Sandbox"], "/#numbers": ["#FFFFFF", "Numbers"], "/#cta": ["#FFFFFF", "Jump"],
  "/app#feed": ["#013330", "Feed"], "/app#map": ["#190066", "Map"], "/app#prove": ["#CEE9FD", "Prove"],
  "/app#ship": ["#FFFFFF", "Ship"], "/app#ledger": ["#EAC2FF", "Ledger"],
};

let shell = null;

/**
 * page: { jump(hash) -> Promise|void, current() -> hash, commands() -> [{label, hint, run}],
 *         theme(hash) -> [color,label] optional }
 */
export function initShell(page) {
  const cursor = initCursor();
  const canvas = $("#keystone");
  const keystone = createKeystone(canvas);
  shell = { page, cursor, keystone };
  on("keystone", (k) => keystone.pulse(k));
  on("keystone-busy", (b) => { keystone.busy(b); cursor?.busy(b); });
  canvas.removeAttribute("aria-hidden");
  canvas.setAttribute("role", "button");
  canvas.setAttribute("aria-label", "Punchy: take me to the machine");
  canvas.addEventListener("click", (e) => {
    if (!canvas.classList.contains("is-docked")) return;
    keystone.pulse("card");
    go(PAGE === "machine" ? (store.session?.user ? "/app#map" : "/app#feed") : "/app#feed", e.clientX);
  });

  // every [data-go] link: same page -> shutter + jump; other page -> shutter + load
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-go]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault();
    closeMenu();
    $("#idPop").hidden = true;
    go(a.dataset.go, e.clientX);
  });
  window.addEventListener("popstate", () => {
    if (PAGE !== "machine") return;
    const hash = location.hash || "#feed";
    if (page.current?.() === hash) return;
    const [color, label] = page.theme?.("/app" + hash) || DEST["/app" + hash] || ["#013330", ""];
    transition(() => page.jump(hash, { history: false }), { color, label });
  });

  $("#menuBtn").addEventListener("click", () => (document.body.classList.contains("menu-open") ? closeMenu() : openMenu()));
  const pop = $("#idPop");
  $("#idChip").addEventListener("click", (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; });
  document.addEventListener("click", (e) => { if (!pop.hidden && !e.target.closest("#idPop")) pop.hidden = true; });
  initPalette();
  markNav();
  return shell;
}

/** Navigate with a transition. `to` is "/#how", "/app#map", "#map" (same page) … */
export function go(to, x = null) {
  const here = PAGE === "machine" ? "/app" : "/";
  let path = to.startsWith("#") ? here : to.split("#")[0] || "/";
  const hash = to.includes("#") ? "#" + to.split("#")[1] : (path === "/app" ? "#feed" : "#top");
  if (path !== "/" && path !== "/app") path = "/";
  const key = path + hash;
  const [color, label] = shell.page.theme?.(key) || DEST[key] || ["#013330", ""];
  if (path === here) {
    return transition(() => shell.page.jump(hash), { color, label, x });
  }
  return leave(key, { color, label, x });
}

export function markNav(hash) {
  const here = PAGE === "machine" ? "/app" : "/";
  $$(".rail a[data-go]").forEach((a) => {
    const g = a.dataset.go;
    const p = g.split("#")[0] || here;
    const on = PAGE === "machine" ? p === "/app" : (hash ? g === "/" + hash : false);
    a.classList.toggle("is-active", on);
  });
}

// ------------------------------------------------------------------ menu
function menuOrigin() {
  const r = $("#menuBtn").getBoundingClientRect();
  return `${Math.round(r.left + r.width / 2)}px ${Math.round(r.top + r.height / 2)}px`;
}
const MOTION = () => !!window.gsap && !reducedMotion();

export function openMenu() {
  document.body.classList.add("menu-open");
  $("#menuBtn").setAttribute("aria-expanded", "true");
  $("#menu").setAttribute("aria-hidden", "false");
  shell?.page.lenis?.()?.stop();
  if (MOTION()) {
    const o = menuOrigin(), g = window.gsap;
    g.fromTo("#menu", { clipPath: `circle(0px at ${o})` }, { clipPath: `circle(${Math.hypot(innerWidth, innerHeight) * 1.05}px at ${o})`, duration: .9, ease: "expo.inOut" });
    g.fromTo(".menu-inner > *", { y: 60, opacity: 0 }, { y: 0, opacity: 1, duration: .8, stagger: .05, ease: "expo.out", delay: .25 });
  } else {
    $("#menu").style.clipPath = "none";
  }
}

export function closeMenu() {
  if (!document.body.classList.contains("menu-open")) return;
  $("#menuBtn").setAttribute("aria-expanded", "false");
  $("#menu").setAttribute("aria-hidden", "true");
  shell?.page.lenis?.()?.start();
  if (MOTION()) window.gsap.to("#menu", { clipPath: `circle(0px at ${menuOrigin()})`, duration: .7, ease: "expo.inOut", overwrite: true, onComplete: () => document.body.classList.remove("menu-open") });
  else document.body.classList.remove("menu-open");
}

// ------------------------------------------------------------------ command palette
let palItems = [], palSel = 0;

function baseCommands() {
  const cmds = [
    { label: "The story", hint: "/", run: () => go("/#top") },
    { label: "How it works", hint: "story", run: () => go("/#how") },
    { label: "How the machine is wired", hint: "story", run: () => go("/#wiring") },
    { label: "Sandbox", hint: "story", run: () => go("/#sandbox") },
    { label: "Open the machine", hint: "/app", run: () => go("/app#feed") },
    { label: "Live map", hint: "machine", run: () => go("/app#map") },
    { label: "Inspector", hint: "machine", run: () => go("/app#prove") },
    { label: "Ship", hint: "machine", run: () => go("/app#ship") },
  ];
  return cmds;
}

export function openPalette(mode = "all") {
  const pal = $("#palette"), input = $("#paletteInput");
  pal.hidden = false;
  pal.dataset.mode = mode;
  input.value = "";
  input.placeholder = mode === "runs" ? "Pick a run to load…" : "Type a command or a run…";
  shell?.page.lenis?.()?.stop();
  renderPalette();
  input.focus();
}
export function closePalette() {
  $("#palette").hidden = true;
  if (!document.body.classList.contains("menu-open")) shell?.page.lenis?.()?.start();
}
function renderPalette() {
  const q = $("#paletteInput").value.toLowerCase().trim();
  const mode = $("#palette").dataset.mode;
  const own = shell?.page.commands?.(mode) || [];
  const all = mode === "runs" ? own : [...own, ...baseCommands()];
  const seen = new Set();
  palItems = all.filter((c) => !seen.has(c.label) && seen.add(c.label))
    .filter((c) => !q || c.label.toLowerCase().includes(q) || (c.hint || "").toLowerCase().includes(q));
  palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  const list = $("#paletteList");
  list.replaceChildren(...palItems.map((c, i) => {
    const li = document.createElement("li");
    li.className = i === palSel ? "is-sel" : "";
    li.innerHTML = `<span></span><small></small>`;
    li.firstChild.textContent = c.label; li.lastChild.textContent = c.hint || "";
    li.addEventListener("mouseenter", () => { palSel = i; renderPalette(); });
    li.addEventListener("click", () => { closePalette(); c.run(); });
    return li;
  }));
  if (!palItems.length) list.innerHTML = `<li><span>No matches</span></li>`;
}
function initPalette() {
  const input = $("#paletteInput");
  input.addEventListener("input", () => { palSel = 0; renderPalette(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { palSel = Math.min(palItems.length - 1, palSel + 1); renderPalette(); e.preventDefault(); }
    if (e.key === "ArrowUp") { palSel = Math.max(0, palSel - 1); renderPalette(); e.preventDefault(); }
    if (e.key === "Enter" && palItems[palSel]) { const c = palItems[palSel]; closePalette(); c.run(); }
  });
  $("#palette").addEventListener("click", (e) => { if (e.target.id === "palette") closePalette(); });
  $("#paletteHint").addEventListener("click", () => openPalette());
  on("palette", (mode) => openPalette(mode));
  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#palette").hidden ? openPalette() : closePalette(); }
    if (e.key === "Escape") { closePalette(); closeMenu(); $("#idPop").hidden = true; const m = $("#reportModal"); if (m) m.hidden = true; }
  });
}

// ------------------------------------------------------------------ identity chip (both pages)
export function renderChip(user, { onSignOut } = {}) {
  $("#idChip").hidden = !user;
  const cta = $("#navCta");
  if (cta) cta.textContent = PAGE === "machine" ? (user ? "New deck" : "Punch in") : (user ? "Open the machine" : "Try the machine");
  if (!user) return;
  $("#idChipName").textContent = user.handle;
  $("#idPopName").textContent = user.handle;
  const pop = $("#idPop");
  pop.onclick = (e) => {
    const act = e.target.closest("button")?.dataset.action;
    pop.hidden = true;
    if (act === "signout") onSignOut?.();
  };
}

/** Load /api/session once (both pages show who is punched in). */
export async function loadSession() {
  store.session = await api("/api/session");
  return store.session;
}

export const getShell = () => shell;
