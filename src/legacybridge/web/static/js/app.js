// The machine page (/app): five stations, one visible at a time. Switching plays the punch-card
// shutter (transition.js) and the station underneath swaps while covered: background, nav ink,
// dock tab, URL hash, then an entrance once the shutter lifts.

import { $, $$, emit, on, reducedMotion, store, toast } from "./api.js";
import { initGitHubPanel, showGitHub } from "./ghpanel.js";
import { getToken } from "./github.js";
import { drawIn, initProduct, moveInk, onSignedIn, paletteRuns, replay, setView, showSource, stampShip } from "./product.js";
import { go, initShell } from "./shell.js";
import { enabled as supabaseOn, initSupabase, signInWithGitHub } from "./supa.js";
import { arrive, arriving, isBusy, reset } from "./transition.js";

const MOTION = !!window.gsap && !reducedMotion();
const STATIONS = ["feed", "map", "prove", "ship", "ledger"];
const arrival = arriving();
const oauthReturn = /[?&]code=/.test(location.search);
let current = null;

// the stations' ids match the hash, so the browser would scroll to them on load: the page stays at the top
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
window.addEventListener("load", () => window.scrollTo(0, 0));

const keyOf = (hash) => {
  const k = String(hash || "").replace(/^#/, "");
  return STATIONS.includes(k) ? k : "feed";
};

/** Show one station. Runs under the shutter when called through go(). */
function switchStation(hash, { history = true, animate = true } = {}) {
  const k = keyOf(hash);
  const sec = $("#" + k);
  const changed = current !== k;
  current = k;
  STATIONS.forEach((s) => { $("#" + s).hidden = s !== k; });
  document.body.style.setProperty("--bg", sec.dataset.bg);
  document.body.dataset.nav = sec.dataset.theme;
  document.body.dataset.station = k;
  $$(".dock a[data-station]").forEach((a) => { const on = a.dataset.station === k; a.classList.toggle("is-on", on); a.setAttribute("aria-current", on ? "page" : "false"); });
  const url = `/app#${k}`;
  if (history === "replace") window.history.replaceState(null, "", url);
  else if (history && location.pathname + location.hash !== url) window.history.pushState(null, "", url);
  window.scrollTo(0, 0);
  // tab strips measure only while visible
  requestAnimationFrame(() => {
    moveInk($("#srcTabs"), $("#srcTabs .is-on"), $("#srcInk"));
    moveInk($(".code-toggle"), $(".code-toggle .is-on"), $("#toggleInk"));
  });
  if (animate && (changed || history)) enter(k, sec);
}

function enter(k, sec) {
  if (k === "map") drawIn();
  if (k === "ship") stampShip();
  if (k === "prove" && store.selected) setView($(".code-toggle .is-on")?.dataset.view || "cobol", false);
  if (!MOTION || document.hidden) return;      // hidden tabs pause rAF: a from-tween would sit at opacity 0
  const g = window.gsap;
  const head = sec.querySelectorAll(".st-head .eyebrow, .st-head h1, .st-head h2, .st-head .section-sub");
  const body = [...sec.children].filter((c) => !c.classList.contains("st-head") && !c.hidden);
  g.killTweensOf([...head, ...body]);
  if (head.length) g.fromTo(head, { y: 50, opacity: 0 }, { y: 0, opacity: 1, duration: .8, stagger: .07, ease: "expo.out", delay: .3, clearProps: "transform,opacity" });
  if (body.length) g.fromTo(body, { y: 80, opacity: 0 }, { y: 0, opacity: 1, duration: .9, stagger: .1, ease: "expo.out", delay: .42, clearProps: "transform,opacity" });
}

function commands(mode) {
  if (mode === "runs") return paletteRuns();
  const st = STATIONS.map((k, i) => ({ label: `Go to ${k[0].toUpperCase() + k.slice(1)}`, hint: `0${i + 1}`, run: () => go("#" + k) }));
  const more = [
    { label: "Replay this run", hint: "map", run: replay },
    { label: "Switch run…", hint: "runs", run: () => setTimeout(() => emit("palette", "runs"), 50) },
  ];
  if (store.session?.user) more.push({ label: "Clone a GitHub repo", hint: "feed", run: () => { go("#feed"); setTimeout(() => showSource("github"), 700); } });
  return [...st, ...more, ...paletteRuns()];
}

async function boot() {
  const shell = initShell({
    jump: (hash, opts) => switchStation(hash, opts),
    current: () => "#" + current,
    lenis: () => null,
    commands,
  });
  $("#keystone").classList.add("is-docked");
  on("go", (hash) => { if (keyOf(hash) !== current) go(hash); });
  on("source", (src) => { if (src === "github") showGitHub(); });
  // number keys 1-5 jump between stations (when not typing)
  window.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || !$("#palette").hidden) return;
    const n = +e.key;
    if (n >= 1 && n <= 5 && !isBusy()) go("#" + STATIONS[n - 1]);
  });
  switchStation(location.hash || "#feed", { history: "replace", animate: !arrival });
  initGitHubPanel();
  const ok = await initProduct();
  if (ok && supabaseOn()) {
    $("#oauthBox").hidden = false;
    $("#ghLogin").addEventListener("click", () => signInWithGitHub().catch((e) => toast(e.message, { tag: "github", bad: true })));
    initSupabase({ onSignedIn }).then(() => {
      if (oauthReturn && store.session.user && getToken()) { showSource("github"); }
    }).catch(() => {});
  }
  shell.keystone?.pulse?.("card");
}

/** Whatever happened during boot, the page must end up visible and clickable. */
let unlocked = false;
async function unlock() {
  if (unlocked) return;
  unlocked = true;
  document.body.classList.remove("is-loading");
  window.scrollTo(0, 0);
  if (arrival && document.documentElement.classList.contains("tx-pre")) {
    await Promise.race([arrive(arrival), new Promise((r) => setTimeout(r, 1600))]);
    enter(current, $("#" + current));
  }
  if (!isBusy()) reset();
}
const watchdog = setTimeout(unlock, 4000);
boot().catch((e) => console.error(e)).finally(() => { clearTimeout(watchdog); unlock(); });
