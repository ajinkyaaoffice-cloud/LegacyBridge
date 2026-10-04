// The machine: the hopper (Feed), the live graph (Map), the inspector (Prove), results (Ship) and
// your runs (Ledger). Every station renders whatever run `store.run` is. Station switching lives in
// app.js; this module asks for it with emit("go", "#station").

import { $, $$, STAGE_PROGRESS, api, emit, fmtAgo, h, on, store, toast } from "./api.js";
import { kindOf } from "./github.js";
import { paint } from "./highlight.js";
import { escapeHtml, initIdCard, ledgerHoles } from "./punchcard.js";
import { renderChip } from "./shell.js";
import { supabaseSignOut } from "./supa.js";

const EXEC = (u) => u.kind !== "copybook";
const NODE_W = 210, NODE_H = 78, COL_GAP = 120, ROW_GAP = 22, PAD_X = 30, PAD_Y = 46;
const TERMINAL = new Set(["verified", "blocked", "dep_blocked"]);
const WANT = /\.(cbl|cob|cobol|cpy|copy|txt)$/i;
let deck = [];            // files in the hopper: {name, content, size, from?} | {demo:true}
let deckTitle = null;     // e.g. the GitHub repo the deck came from
let engine = "offline";
let es = null, pollTimer = 0, refreshTimer = 0, idCard = null;

// ------------------------------------------------------------------ boot
export async function initProduct() {
  idCard = initIdCard({ onSignedIn });
  wireSources();
  wireHopper();
  wireInspector();
  wireShip();
  wireRunSwitch();
  on("run-changed", renderAll);
  try {
    store.session = await api("/api/session");
  } catch {
    toast("The LegacyBridge server is not answering. Start it with `legacybridge web`.", { tag: "offline", bad: true, ms: 9000 });
    return false;
  }
  if (!store.session.cobc) toast(store.session.cobc_note, { tag: "cobc", bad: true, ms: 12000 });
  renderIdentity();
  if (store.session.user) await refreshRuns();
  const mine = store.runs[0];
  await loadRun(mine ? mine.id : store.session.specimen);
  return true;
}

export function onSignedIn(user, isNew) {
  const was = store.session.user;
  if (was && was.handle === user.handle) return;     // Supabase can report the same sign-in twice
  store.session.user = user;
  renderIdentity(true);
  toast(isNew ? `Card punched. Welcome, ${user.handle}.` : `Punched in as ${user.handle}.`, { tag: "reader" });
  refreshRuns().then(renderLedger);
  emit("signed-in", user);
}

export async function signOut() {
  await api("/api/auth/signout", { method: "POST" }).catch(() => {});
  await supabaseSignOut();
  store.session.user = null;
  store.runs = [];
  deck = []; deckTitle = null;
  idCard.reset();
  renderIdentity();
  if (store.run && !store.run.public) await loadRun(store.session.specimen);
  toast("Punched out. The card is yours to keep.", { tag: "reader" });
  emit("go", "#feed");
}

async function refreshRuns() {
  if (!store.session?.user) { store.runs = []; return; }
  try { store.runs = await api("/api/runs"); } catch { store.runs = []; }
}

// ------------------------------------------------------------------ identity: card vs hopper
function renderIdentity(animate = false) {
  const user = store.session?.user;
  $("#signin").hidden = !!user;
  $("#hopper").hidden = !user;
  renderChip(user, { onSignOut: signOut });
  $("#feedSub").textContent = user
    ? "Drop COBOL programs, copybooks and scenario files, clone a GitHub repo, or take the bank demo for a spin."
    : "Your first card is you. Punch it, feed it, and the hopper opens for your COBOL.";
  $("#readerLabel").textContent = user ? "HOPPER OPEN · AWAITING DECK" : "READER READY · INSERT CARD";
  if (user) {
    $("#helloName").textContent = user.handle;
    renderEngine();
    renderDeck();
    moveInk($("#srcTabs"), $("#srcTabs .is-on"), $("#srcInk"));
    if (animate && window.gsap) window.gsap.from("#hopper > *", { y: 60, opacity: 0, duration: .9, stagger: .12, ease: "expo.out", clearProps: "transform,opacity" });
  }
  renderLedger();
}

function renderEngine() {
  const live = store.session?.live_providers || [];
  const btn = $("#liveBtn");
  btn.disabled = !live.length;
  $("#liveNote").textContent = live.length ? live.join(" · ") : "add a key to .env";
  btn.title = live.length ? "" : "Add GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY or MISTRAL_API_KEY to .env and restart";
  if (!live.length && engine === "live") engine = "offline";
  $$(".seg-btn").forEach((b) => { const on = b.dataset.engine === engine; b.classList.toggle("is-on", on); b.setAttribute("aria-checked", on); });
  renderWarn();
}

// ------------------------------------------------------------------ sliding ink under a tab strip
export function moveInk(strip, btn, ink) {
  if (!strip || !btn || !ink || !strip.offsetWidth) return;
  ink.style.width = btn.offsetWidth + "px";
  ink.style.transform = `translateX(${btn.offsetLeft}px)`;
}

// ------------------------------------------------------------------ hopper sources
function wireSources() {
  const tabs = $("#srcTabs");
  tabs.addEventListener("click", (e) => {
    const b = e.target.closest("[data-src]");
    if (b) showSource(b.dataset.src);
  });
  window.addEventListener("resize", () => {
    moveInk(tabs, $("#srcTabs .is-on"), $("#srcInk"));
    moveInk($(".code-toggle"), $(".code-toggle .is-on"), $("#toggleInk"));
  });
}

export function showSource(src) {
  const btn = $(`#srcTabs [data-src="${src}"]`);
  if (!btn) return;
  $$("#srcTabs [data-src]").forEach((b) => { b.classList.toggle("is-on", b === btn); b.setAttribute("aria-selected", b === btn); });
  moveInk($("#srcTabs"), btn, $("#srcInk"));
  const pane = $(`.src-pane[data-pane="${src}"]`);
  const prev = $(".src-pane.is-on");
  if (prev === pane) return;
  $$(".src-pane").forEach((p) => p.classList.toggle("is-on", p === pane));
  if (window.gsap) window.gsap.fromTo(pane, { clipPath: "inset(0 0 100% 0 round 28px)", y: 18 }, { clipPath: "inset(0 0 0% 0 round 28px)", y: 0, duration: .6, ease: "expo.out", clearProps: "clipPath,transform" });
  emit("source", src);
}

// ------------------------------------------------------------------ hopper
function wireHopper() {
  const drop = $("#drop"), input = $("#fileInput");
  $("#pickFiles").addEventListener("click", (e) => { e.stopPropagation(); input.click(); });
  $("#useDemo").addEventListener("click", () => { deck = [{ demo: true, name: "bank_demo" }]; deckTitle = null; renderDeck(true); });
  drop.addEventListener("click", (e) => { if (!e.target.closest("button")) input.click(); });
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { addFiles([...input.files]); input.value = ""; });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
  drop.addEventListener("drop", (e) => addFiles([...(e.dataTransfer?.files || [])]));
  $$(".seg-btn").forEach((b) => b.addEventListener("click", () => { if (!b.disabled) { engine = b.dataset.engine; renderEngine(); } }));
  $("#fuzzToggle").addEventListener("change", renderWarn);
  $("#runBtn").addEventListener("click", startRun);
}

async function addFiles(files) {
  const bad = files.filter((f) => !WANT.test(f.name));
  if (bad.length) toast(`Skipped ${bad.map((f) => f.name).join(", ")}: not COBOL, copybook or scenario text.`, { tag: "hopper", bad: true });
  const add = [];
  for (const f of files.filter((f) => WANT.test(f.name))) {
    if (f.size > 512 * 1024) { toast(`${f.name} is larger than 512 KB.`, { tag: "hopper", bad: true }); continue; }
    add.push({ name: f.name, content: await f.text(), size: f.size });
  }
  addToDeck(add);
}

/** Put files into the hopper (uploads, or files picked from a cloned repo). Same names replace. */
export function addToDeck(files, { title = null, replace = false } = {}) {
  if (replace || deck[0]?.demo) deck = [];
  for (const f of files) {
    deck = deck.filter((d) => d.name.toLowerCase() !== f.name.toLowerCase());
    deck.push(f);
  }
  deckTitle = title ?? deckTitle;
  renderDeck(true);
}

function renderDeck(animate = false) {
  const stack = $("#hopperStack");
  stack.replaceChildren();
  deck.forEach((d, i) => {
    const card = d.demo
      ? h("div", { class: "deck-file is-demo" }, h("span", { text: "bank_demo/" }), h("small", { text: "11 programs · 8 copybooks · 4 scenarios" }))
      : h("div", { class: `deck-file k-${kindOf(d.name)}`, title: d.from || d.name }, h("span", { text: d.name }), h("small", { text: `${kindOf(d.name)} · ${d.content.split("\n").length} lines` }));
    card.append(h("button", { type: "button", "aria-label": `Remove ${d.name}`, text: "×", onclick: (e) => { e.stopPropagation(); deck.splice(i, 1); if (!deck.length) deckTitle = null; renderDeck(); } }));
    stack.append(card);
  });
  if (!deck.length) stack.append(h("p", { class: "tray-empty mono", text: "EMPTY TRAY" }));
  if (animate && window.gsap && deck.length) {
    window.gsap.from(stack.children, { y: -80, rotate: () => (Math.random() - .5) * 16, opacity: 0, duration: .6, stagger: .035, ease: "back.out(1.4)", clearProps: "transform,opacity" });
  }
  const programs = deck.filter((d) => d.demo || kindOf(d.name) === "program").length;
  $("#deckSummary").textContent = !deck.length ? "The hopper is empty."
    : deck[0].demo ? "The bank demo is loaded: a batch banking system with a CALL cycle, COMP-3 and page breaks."
      : `${deckTitle ? deckTitle + ": " : ""}${deck.length} card${deck.length > 1 ? "s" : ""}, ${programs} program${programs === 1 ? "" : "s"}.`;
  $("#runBtn").disabled = !programs;
  renderWarn();
}

function renderWarn() {
  const warn = $("#hopperWarn");
  const upload = deck.length && !deck[0].demo;
  const msgs = [];
  if (upload && engine === "offline") msgs.push("The offline mock only knows the bank demo's programs. Your own code will come back BLOCKED unless you switch to free-tier LLMs.");
  if (store.session && !store.session.cobc) msgs.push(store.session.cobc_note);
  warn.hidden = !msgs.length;
  warn.textContent = msgs.join(" ");
}

async function startRun() {
  const btn = $("#runBtn");
  if (btn.disabled) return;
  const body = { source: deck[0]?.demo ? "demo" : "upload", fuzz: $("#fuzzToggle").checked, live: engine === "live" };
  if (body.source === "upload") body.files = deck.map(({ name, content }) => ({ name, content }));
  if (deckTitle) body.title = deckTitle.slice(0, 80);
  btn.style.setProperty("--lw", btn.offsetWidth + "px");
  btn.classList.add("is-pulled");
  btn.disabled = true;
  btn.querySelector(".lever-text").textContent = "Feeding…";
  try {
    const run = await api("/api/runs", { method: "POST", body });
    emit("keystone", "card");
    toast(`Deck accepted. Run ${run.id} is in the machine.`, { tag: "run" });
    deck = []; deckTitle = null;
    await refreshRuns();
    await loadRun(run.id);
    emit("go", "#map");
  } catch (e) {
    toast(e.message, { tag: "run", bad: true, ms: 7000 });
  } finally {
    setTimeout(() => {
      btn.classList.remove("is-pulled");
      btn.querySelector(".lever-text").textContent = "Pull to migrate";
      renderDeck();
    }, 900);
  }
}

// ------------------------------------------------------------------ loading a run
export async function loadRun(id, { quiet = false } = {}) {
  closeStream();
  store.units.clear();
  store.selected = null;
  try {
    const [run, snapshot] = await Promise.all([api(`/api/runs/${id}`), api(`/api/runs/${id}/state`)]);
    store.run = run; store.snapshot = snapshot;
  } catch (e) {
    if (!quiet) toast(`Could not load run ${id}: ${e.message}`, { tag: "run", bad: true });
    return;
  }
  emit("run-changed");
  if (isActive()) openStream();
}

const isActive = () => store.run && ["queued", "running"].includes(store.run.status);

function openStream() {
  const id = store.run.id;
  es = new EventSource(`/api/runs/${id}/stream?after=${store.snapshot?.last_event_id || 0}`);
  es.addEventListener("ev", (m) => onEvent(JSON.parse(m.data)));
  es.addEventListener("reset", () => scheduleRefresh(0));
  emit("keystone-busy", true);
  pollTimer = setInterval(async () => {
    try {
      const run = await api(`/api/runs/${id}`);
      if (store.run?.id !== id) return;
      store.run = run;
      renderRunbar();
      if (!isActive()) {
        closeStream();
        await refreshSnapshot();
        await refreshRuns();
        renderLedger();
        const s = run.summary || {};
        const good = run.status === "done" && s.verified === s.units && s.scenarios_identical === s.scenarios;
        emit("keystone", good ? "ok" : "bad");
        toast(run.status === "failed" ? `Run ${id} failed: ${(run.note || "").split("\n").slice(-1)[0]}`
          : `Run ${id} finished: ${s.verified}/${s.units} units verified, ${s.scenarios_identical}/${s.scenarios} scenarios identical.`,
        { tag: run.status === "failed" ? "failed" : "done", bad: !good, ms: 8000 });
      }
    } catch { /* keep polling */ }
  }, 2000);
}

function closeStream() {
  if (es) { es.close(); es = null; }
  clearInterval(pollTimer);
  emit("keystone-busy", false);
}

function scheduleRefresh(ms = 900) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshSnapshot, ms);
}

async function refreshSnapshot() {
  if (!store.run) return;
  try {
    store.snapshot = await api(`/api/runs/${store.run.id}/state`);
    renderGraph(); renderStats(); renderUnitTabs(); renderShip(); renderDock();
    if (store.selected) loadUnit(store.selected, { force: true, keepView: true });
  } catch { /* transient */ }
}

function onEvent(ev) {
  if (store.replaying) return;
  applyEvent(ev, { live: true });
  if (["status", "verify", "fuzz_round", "fuzz_bug", "oracle", "lesson_added", "scenario", "integrated", "analyzed"].includes(ev.type)) scheduleRefresh();
}

// one event -> visual change (used live and in replay)
function applyEvent(ev, { live = false } = {}) {
  const u = ev.unit;
  if (ev.type === "status" && u) {
    setNodeStatus(u, ev.status);
    if (ev.status === "verified" && live) emit("keystone", "ok");
    if ((ev.status === "blocked" || ev.status === "dep_blocked") && live) emit("keystone", "bad");
    if (["translating", "repairing", "fuzzing", "verified", "blocked"].includes(ev.status)) tick(u, ev.status, ev.status === "blocked");
  } else if (ev.type === "verify") {
    tick(u, `verify ${ev.passed}/${ev.total}`, !ev.ok);
    if (!live) emit("keystone", ev.ok ? "ok" : "bad");
  } else if (ev.type === "oracle") {
    tick(u, `oracle ${ev.module}: ${ev.total} golden tests`);
  } else if (ev.type === "fuzz_bug") {
    tick(u, `fuzz found ${ev.counterexamples} counterexample(s)`, true);
  } else if (ev.type === "fuzz_round") {
    tick(u, `fuzz round ${ev.round} clean (${ev.clean}/${ev.needed})`);
  } else if (ev.type === "lesson_added") {
    tick(u, `lesson #${ev.lesson_id} learned`);
  } else if (ev.type === "scenario") {
    tick(ev.module || "scenario", `${ev.name || ""} ${ev.passed ? "IDENTICAL" : "DIFFERS"}`, !ev.passed);
  }
}

function tick(unit, text, bad = false) {
  const t = $("#ticker");
  t.prepend(h("span", { class: bad ? "bad" : "" }, h("b", { text: unit || "" }), " " + text));
  while (t.children.length > 6) t.lastChild.remove();
}

// ------------------------------------------------------------------ replay
export async function replay() {
  if (!store.run || store.replaying || isActive()) return;
  const id = store.run.id;
  let events;
  try { events = await api(`/api/runs/${id}/events?limit=5000`); } catch { return; }
  events = events.filter((e) => e.type !== "llm_call");
  if (!events.length) return;
  emit("go", "#map");
  store.replaying = true;
  $("#replayBtn").disabled = true;
  $("#ticker").replaceChildren();
  $$(".graph .node").forEach((n) => setNodeStatus(n.dataset.unit, "pending", false));
  const stats = { tests: 0, attempts: 0, fuzz: 0, lessons: 0, verified: 0 };
  paintStats(stats);
  await new Promise((r) => setTimeout(r, 900));      // let the station switch finish first
  const t0 = events[0].ts, span = Math.max(1, events[events.length - 1].ts - t0);
  const total = Math.min(11000, Math.max(5000, events.length * 45));
  const start = performance.now();
  emit("keystone-busy", true);
  for (const ev of events) {
    if (store.run?.id !== id) break;
    const due = ((ev.ts - t0) / span) * total;
    const wait = due - (performance.now() - start);
    if (wait > 8) await new Promise((r) => setTimeout(r, wait));
    applyEvent(ev);
    if (ev.type === "oracle") stats.tests += ev.added || 0;
    if (ev.type === "fuzz_round") stats.tests += ev.new_tests || 0;
    if (ev.type === "verify") stats.attempts += 1;
    if (ev.type === "fuzz_bug") stats.fuzz += 1;
    if (ev.type === "lesson_added") stats.lessons += 1;
    if (ev.type === "status" && ev.status === "verified" && ev.unit && nodeExists(ev.unit)) stats.verified += 1;
    paintStats(stats);
  }
  emit("keystone-busy", false);
  store.replaying = false;
  $("#replayBtn").disabled = false;
  if (store.run?.id === id) { renderGraph(); renderStats(); }
}

// ------------------------------------------------------------------ render all
function renderAll() {
  renderRunbar(); renderGraph(); renderStats(); renderUnitTabs(); renderShip(); renderLedger(); renderDock();
  const units = (store.snapshot?.units || []).filter(EXEC);
  const pick = [...units].sort((a, b) => (b.attempts || 0) + (b.fuzz_bugs_found || 0) * 2 - (a.attempts || 0) - (a.fuzz_bugs_found || 0) * 2)[0];
  if (pick) loadUnit(pick.id);
  else showEmptyInspector();
}

function renderRunbar() {
  const r = store.run;
  if (!r) return;
  $("#runTitle").textContent = r.public ? "Specimen · bank_demo" : `${r.title} · ${r.id}`;
  const s = r.summary || {};
  const bits = [r.dry_run ? "offline mock" : "free-tier LLMs", r.fuzz ? "fuzzing on" : "no fuzzing"];
  if (isActive()) bits.unshift(r.status === "queued" ? "queued" : "running");
  else if (r.status === "failed") bits.unshift("failed");
  else if (s.units) bits.unshift(`${s.verified}/${s.units} verified`);
  if (r.finished_at) bits.push(fmtAgo(r.finished_at));
  $("#runMeta").textContent = bits.join(" · ");
  $("#liveDot").classList.toggle("is-live", isActive());
  $("#replayBtn").hidden = isActive();
}

// ------------------------------------------------------------------ graph
function layout(snapshot) {
  const units = snapshot.units.filter(EXEC);
  const ids = new Set(units.map((u) => u.id));
  const deps = new Map(units.map((u) => [u.id, []]));
  for (const e of snapshot.edges) if (ids.has(e.source) && ids.has(e.target)) deps.get(e.source).push(e.target);
  const wave = new Map();
  const visit = (id, seen = new Set()) => {
    if (wave.has(id)) return wave.get(id);
    if (seen.has(id)) return 0;
    seen.add(id);
    const w = deps.get(id).length ? 1 + Math.max(...deps.get(id).map((d) => visit(d, seen))) : 0;
    wave.set(id, w);
    return w;
  };
  units.forEach((u) => visit(u.id));
  const cols = [];
  units.forEach((u) => { const w = wave.get(u.id); (cols[w] ||= []).push(u); });
  cols.forEach((c) => c.sort((a, b) => a.id.localeCompare(b.id)));
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const height = PAD_Y + rows * NODE_H + (rows - 1) * ROW_GAP + 20;
  const pos = new Map();
  cols.forEach((col, w) => {
    const colH = col.length * NODE_H + (col.length - 1) * ROW_GAP;
    const y0 = PAD_Y + (height - PAD_Y - 20 - colH) / 2;
    col.forEach((u, i) => pos.set(u.id, { x: PAD_X + w * (NODE_W + COL_GAP), y: y0 + i * (NODE_H + ROW_GAP), wave: w }));
  });
  const width = PAD_X * 2 + cols.length * NODE_W + (cols.length - 1) * COL_GAP;
  const edges = [];
  units.forEach((u) => deps.get(u.id).forEach((d) => {
    const kind = snapshot.edges.find((e) => e.source === u.id && e.target === d)?.kind || "call";
    edges.push({ from: d, to: u.id, kind });
  }));
  return { units, pos, width, height, cols: cols.length, edges };
}

const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}) => { const el = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };

function renderGraph() {
  const svg = $("#graph");
  const snap = store.snapshot;
  svg.replaceChildren();
  if (!snap || !snap.units.length) {
    svg.setAttribute("viewBox", "0 0 860 200");
    svg.style.minWidth = "";
    const t = s("text", { x: 430, y: 100, "text-anchor": "middle", class: "wave-label" });
    t.textContent = store.run && isActive() ? "ANALYZING THE DECK…" : "NO UNITS YET";
    svg.append(t);
    return;
  }
  const L = layout(snap);
  svg.setAttribute("viewBox", `0 0 ${L.width} ${L.height}`);
  svg.style.minWidth = Math.min(L.width, 1400) * .75 + "px";
  for (let w = 0; w < L.cols; w++) {
    const t = s("text", { x: PAD_X + w * (NODE_W + COL_GAP), y: 20, class: "wave-label" });
    t.textContent = `WAVE ${w}`;
    svg.append(t);
  }
  const eg = s("g");
  for (const e of L.edges) {
    const a = L.pos.get(e.from), b = L.pos.get(e.to);
    const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2, mx = (x1 + x2) / 2;
    eg.append(s("path", { d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, class: `edge ${e.kind}`, "data-from": e.from, "data-to": e.to, "data-wave": b.wave }));
  }
  svg.append(eg);
  for (const u of L.units) {
    const p = L.pos.get(u.id);
    const g = s("g", { class: "node" + (u.id === store.selected ? " is-sel" : ""), transform: `translate(${p.x},${p.y})`, "data-unit": u.id, "data-s": u.status, "data-wave": p.wave, tabindex: 0, role: "button", "aria-label": `${u.id}: ${u.status}` });
    // a little punch card: cut corner, two rows of holes
    g.append(s("path", { class: "body", d: `M14,0 H${NODE_W - 12} Q${NODE_W},0 ${NODE_W},12 V${NODE_H - 12} Q${NODE_W},${NODE_H} ${NODE_W - 12},${NODE_H} H12 Q0,${NODE_H} 0,${NODE_H - 12} V14 Z` }));
    const holes = s("g", { class: "holes" });
    [...u.id].slice(0, 14).forEach((ch, i) => { const c = ch.charCodeAt(0); holes.append(s("rect", { x: NODE_W - 16 - i * 7, y: 8 + (c % 3) * 5, width: 3, height: 3, rx: .8 })); });
    const name = s("text", { x: 16, y: 34 }); name.textContent = u.id.length > 20 ? u.id.slice(0, 19) + "…" : u.id;
    const sub = s("text", { x: 16, y: 52, class: "sub" }); sub.textContent = statusLine(u);
    g.append(holes, name, sub, s("rect", { class: "bar-bg", x: 16, y: 62, width: NODE_W - 32, height: 5, rx: 2.5 }),
      s("rect", { class: "bar", x: 16, y: 62, width: (NODE_W - 32) * (STAGE_PROGRESS[u.status] ?? 0), height: 5, rx: 2.5 }));
    g.addEventListener("click", () => selectFromGraph(u.id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter") selectFromGraph(u.id); });
    g.addEventListener("mouseenter", () => hotEdges(u.id, true));
    g.addEventListener("mouseleave", () => hotEdges(u.id, false));
    svg.append(g);
  }
}

/** Entrance for the map station: waves of cards drop in, then the wires draw between them. */
export function drawIn() {
  const g = window.gsap;
  if (!g || document.hidden) return;
  const nodes = $$(".graph .node"), edges = $$(".graph .edge"), labels = $$(".graph .wave-label");
  g.killTweensOf([...nodes, ...edges, ...labels]);
  if (!nodes.length) return;
  g.fromTo(labels, { opacity: 0, y: -10 }, { opacity: 1, y: 0, duration: .5, stagger: .08, ease: "expo.out", delay: .15 });
  nodes.forEach((n) => {
    const w = +n.dataset.wave || 0;
    g.fromTo(n, { opacity: 0, y: -26 }, { opacity: 1, y: 0, duration: .6, ease: "back.out(1.6)", delay: .2 + w * .16 + Math.random() * .08, clearProps: "opacity,transform" });
  });
  edges.forEach((p) => {
    const len = p.getTotalLength?.() || 300;
    const w = +p.dataset.wave || 0;
    g.fromTo(p, { strokeDasharray: len, strokeDashoffset: len }, { strokeDashoffset: 0, duration: .7, ease: "power2.inOut", delay: .45 + w * .16, clearProps: "strokeDasharray,strokeDashoffset" });
  });
}

function statusLine(u) {
  const bits = [u.status.replace("_", " ")];
  if (u.attempts) bits.push(`${u.attempts} att`);
  if (u.fuzz_bugs_found) bits.push(`${u.fuzz_bugs_found} fuzz bug`);
  return bits.join(" · ");
}

const nodeExists = (id) => !!document.querySelector(`.graph .node[data-unit="${CSS.escape(id)}"]`);

function setNodeStatus(id, status, flash = true) {
  const g = document.querySelector(`.graph .node[data-unit="${CSS.escape(id)}"]`);
  if (!g) return;
  g.dataset.s = status;
  const bar = g.querySelector(".bar");
  if (bar) bar.setAttribute("width", (NODE_W - 32) * (STAGE_PROGRESS[status] ?? 0));
  const sub = g.querySelector(".sub");
  if (sub) sub.textContent = status.replace("_", " ");
  if (flash) { g.classList.remove("is-flash"); void g.getBBox(); g.classList.add("is-flash"); }
  $$(`.unit-tabs button[data-unit="${CSS.escape(id)}"]`).forEach((b) => { b.dataset.s = status; });
}

function hotEdges(id, on) {
  $$(".graph .edge").forEach((p) => { if (p.dataset.from === id || p.dataset.to === id) p.classList.toggle("is-hot", on); });
}

function selectFromGraph(id) {
  loadUnit(id);
  emit("go", "#prove");
}

// ------------------------------------------------------------------ stats (Prove bento + Ship)
function computeStats() {
  const snap = store.snapshot;
  if (!snap) return null;
  const units = snap.units.filter(EXEC);
  const tests = Object.entries(snap.tests || {}).filter(([o]) => o !== "hidden").reduce((a, [, n]) => a + n, 0);
  return {
    tests, attempts: units.reduce((a, u) => a + (u.attempts || 0), 0),
    fuzz: units.reduce((a, u) => a + (u.fuzz_bugs_found || 0), 0), lessons: (snap.lessons || []).length,
    verified: units.filter((u) => u.status === "verified").length, total: units.length,
    byOrigin: snap.tests || {},
  };
}

function paintStats(st) {
  const set = (k, v) => $$(`[data-stat="${k}"]`).forEach((el) => { el.textContent = v; });
  set("tests", st.tests.toLocaleString());
  set("attempts", st.attempts);
  set("fuzz", st.fuzz);
  set("lessons", st.lessons);
  set("verified", `${st.verified}/${st.total ?? store.snapshot?.units.filter(EXEC).length ?? 0}`);
}

function renderStats() {
  const st = computeStats();
  if (!st) return;
  paintStats(st);
  const o = st.byOrigin;
  const fuzzT = (o.fuzz_llm || 0) + (o.fuzz_mutation || 0);
  const note = (k, v) => $$(`[data-stat="${k}"]`).forEach((el) => { el.textContent = v; });
  note("tests_note", `${o.llm || 0} llm · ${o.property || 0} boundary · ${o.random || 0} random · ${fuzzT} fuzz`);
  note("attempts_note", st.total ? `${(st.attempts / Math.max(1, st.total)).toFixed(1)} per unit on average` : "translate + repairs");
  note("fuzz_note", st.fuzz ? "each one became a golden test, then got repaired" : "after the golden tests passed");
  const lesson = (store.snapshot.lessons || [])[0]?.text;
  note("lessons_note", lesson ? lesson.slice(0, 64) + (lesson.length > 64 ? "…" : "") : "reused by later units");
}

function renderDock() {
  const st = computeStats();
  if (!st) return;
  const active = isActive();
  const set = (k, txt, live, done) => {
    const a = $(`.dock a[data-station="${k}"]`);
    if (!a) return;
    a.querySelector("b").textContent = txt || "";
    a.classList.toggle("is-live", !!live);
    a.classList.toggle("is-done", !!done && !live);
  };
  const all = st.total && st.verified === st.total;
  set("map", st.total ? `${st.verified}/${st.total}` : "", active, all);
  set("prove", st.tests ? st.tests.toLocaleString() : "", active, all);
  const sc = store.snapshot.scenarios || [];
  set("ship", sc.length ? `${sc.filter((x) => x.passed).length}/${sc.length}` : "", false, sc.length && sc.every((x) => x.passed));
  set("ledger", store.runs.length ? String(store.runs.length) : "", false, false);
}

// ------------------------------------------------------------------ inspector
let view = "cobol";

function wireInspector() {
  $$(".code-toggle button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
}

export function setView(v, animate = true) {
  const prev = view;
  view = v;
  const btn = $(`.code-toggle button[data-view="${v}"]`);
  $$(".code-toggle button").forEach((b) => b.classList.toggle("is-on", b === btn));
  moveInk($(".code-toggle"), btn, $("#toggleInk"));
  const pane = $(`.insp-pane[data-pane="${v}"]`);
  $$(".insp-pane").forEach((p) => p.classList.toggle("is-on", p === pane));
  if (animate && prev !== v && window.gsap) {
    // the pixel wipe of the hero, in CSS: the new pane resolves in steps from coarse blocks
    window.gsap.fromTo(pane, { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: .55, ease: "steps(8)", clearProps: "clipPath" });
  }
}

function renderUnitTabs() {
  const box = $("#unitTabs");
  const units = (store.snapshot?.units || []).filter(EXEC);
  box.replaceChildren(...units.map((u) => h("button", {
    type: "button", "data-unit": u.id, "data-s": u.status, class: u.id === store.selected ? "is-on" : "",
    onclick: () => loadUnit(u.id),
  }, h("i"), u.id)));
}

export async function loadUnit(name, { force = false, keepView = false } = {}) {
  if (!store.run) return;
  store.selected = name;
  $$(".unit-tabs button").forEach((b) => b.classList.toggle("is-on", b.dataset.unit === name));
  $$(".graph .node").forEach((n) => n.classList.toggle("is-sel", n.dataset.unit === name));
  const btn = $(`.unit-tabs button[data-unit="${CSS.escape(name)}"]`);
  if (btn && btn.parentElement.offsetWidth) {   // scroll only the tab strip
    const box = btn.parentElement;
    box.scrollTo({ left: btn.offsetLeft - (box.clientWidth - btn.offsetWidth) / 2, behavior: "smooth" });
  }
  let d = store.units.get(name);
  if (!d || force) {
    try { d = await api(`/api/runs/${store.run.id}/unit/${encodeURIComponent(name)}`); } catch { return; }
    store.units.set(name, d);
  }
  if (store.selected !== name) return;
  renderInspector(d, keepView);
}

function showEmptyInspector() {
  $("#inspName").textContent = isActive() ? "waiting for the analyzer…" : "no units";
  $("#inspStatus").textContent = "–";
  delete $("#inspStatus").dataset.s;
  $("#paneCobol").replaceChildren(h("span", { class: "empty-note", text: "Feed a deck and the inspector fills in as units move through the pipeline." }));
  $("#panePython").replaceChildren();
  $(".insp-pane[data-pane='attempts']").replaceChildren();
  setView("cobol", false);
}

function renderInspector(d, keepView) {
  $("#inspName").textContent = d.name;
  const st = $("#inspStatus");
  st.textContent = d.status.replace("_", " ");
  st.dataset.s = d.status;
  const members = d.members.filter((m) => m.kind !== "copybook");
  paint($("#paneCobol"), members.map((m) => (members.length > 1 ? `      *> ---- ${m.name} ----\n` : "") + (m.source || "")).join("\n"), "cobol");
  const py = members.map((m) => m.python_code ? (members.length > 1 ? `# ---- ${m.name} ----\n` : "") + m.python_code : "").filter(Boolean).join("\n\n");
  paint($("#panePython"), py || (TERMINAL.has(d.status) ? "# no accepted translation" : "# translating… the accepted code appears here once it passes"), "python");
  renderAttempts(d);
  if (!keepView) setView(d.attempts.some((a) => a.verdict !== "pass") ? "attempts" : "cobol", false);
}

function renderAttempts(d) {
  const box = $(".insp-pane[data-pane='attempts']");
  box.replaceChildren();
  if (!d.attempts.length) { box.append(h("div", { class: "empty-note", text: "No attempts yet." })); return; }
  d.attempts.forEach((a, i) => {
    const res = Object.values(a.diff || {});
    const passed = res.reduce((x, r) => x + (r.passed || 0), 0), total = res.reduce((x, r) => x + (r.total || 0), 0);
    const ok = a.verdict === "pass";
    const card = h("div", { class: "att", style: { animationDelay: `${i * 60}ms` } },
      h("div", { class: "att-top" },
        h("span", { class: `stamp ${ok ? "ok" : "bad"}`, text: ok ? "pass" : a.verdict || "fail" }),
        h("span", { text: `#${a.attempt_no} ${a.kind}${a.stage === "fuzz" ? " · after fuzz" : ""}` }),
        h("span", { text: `${passed}/${total} tests` }),
        h("span", { class: "att-model", text: a.model || "" })));
    for (const [mod, r] of Object.entries(a.diff || {})) {
      if (r.static_error) card.append(h("div", { class: "att-diff" }, h("span", { class: "act", text: `${mod}: ${r.static_error}` })));
      const ff = r.first_failure;
      if (!ff) continue;
      const diff = h("div", { class: "att-diff" });
      diff.innerHTML = `<span class="lbl">${escapeHtml(mod)} · first difference${ff.line ? ` at line ${ff.line}` : ""}${ff.kind ? ` (${escapeHtml(ff.kind)})` : ""}</span>\n`
        + `<span class="lbl">expected </span><span class="exp">${showWs(ff.expected)}</span>\n`
        + `<span class="lbl">actual   </span><span class="act">${showWs(ff.actual)}</span>`;
      card.append(diff);
      if (ff.input) card.append(h("div", { class: "att-input", "data-lenis-prevent": "", text: `input (${ff.name || "test"}):\n${ff.input}` }));
    }
    box.append(card);
  });
}

function showWs(v) {
  if (v == null) return "∅";
  const str = typeof v === "string" ? v : JSON.stringify(v);
  const m = str.match(/^(.*?)( +)$/);
  return m ? `${escapeHtml(m[1])}<span class="ws">${"·".repeat(m[2].length)}</span>` : escapeHtml(str);
}

// ------------------------------------------------------------------ ship
function wireShip() {
  $("#reportBtn").addEventListener("click", async () => {
    try {
      const res = await fetch(`/api/runs/${store.run.id}/report`, { credentials: "same-origin" });
      if (!res.ok) throw new Error("no report yet");
      $("#reportText").textContent = await res.text();
      $("#reportModal").hidden = false;
    } catch (e) { toast(e.message, { tag: "report", bad: true }); }
  });
  $("#reportModal").addEventListener("click", (e) => { if (e.target.id === "reportModal" || e.target.closest(".modal-close")) $("#reportModal").hidden = true; });
  $("#downloadBtn").addEventListener("click", (e) => { if (e.currentTarget.getAttribute("aria-disabled") === "true") e.preventDefault(); });
}

function renderShip() {
  const run = store.run, snap = store.snapshot;
  if (!run || !snap) return;
  const box = $("#scenarios");
  const sc = snap.scenarios || [];
  box.replaceChildren(...(sc.length ? sc.map((x) => h("div", { class: "scen" },
    h("span", { text: `${x.module} · ${x.name}` }),
    h("span", { class: `stamp ${x.passed === true ? "ok" : x.passed === false ? "bad" : "skip"}`, text: x.passed === true ? "identical" : x.passed === false ? "differs" : "skipped" })))
    : [h("div", { class: "scen" }, h("span", { text: isActive() ? "scenarios replay after the last unit is verified" : "no end-to-end scenarios in this deck" }))]));
  const ok = sc.filter((x) => x.passed).length;
  $("#scenNum").textContent = `${ok}/${sc.length}`;
  const units = snap.units.filter(EXEC);
  const verified = units.filter((u) => u.status === "verified").length;
  const stamp = $("#verdictStamp");
  const good = units.length && verified === units.length && ok === sc.length;
  stamp.textContent = isActive() ? "in the machine" : run.status === "failed" ? "failed" : good ? "proven" : units.length ? "partial" : "–";
  stamp.className = "stamp " + (isActive() ? "skip" : good ? "ok" : "bad");
  $("#verdict").className = "verdict " + (isActive() ? "c-sky" : good ? "c-green" : units.length ? "c-peach" : "c-sky");
  const dl = $("#downloadBtn");
  dl.href = run.has_package ? `/api/runs/${run.id}/download` : "#";
  dl.setAttribute("aria-disabled", run.has_package ? "false" : "true");
  $("#reportBtn").disabled = !run.has_package;
}

/** Entrance for the ship station: slam the verdict stamp and the scenario stamps. */
export function stampShip() {
  [$("#verdictStamp"), ...$$("#scenarios .stamp")].forEach((el, i) => {
    el.classList.remove("is-stamped"); void el.offsetWidth;
    el.style.animationDelay = `${250 + i * 110}ms`;
    el.classList.add("is-stamped");
  });
}

// ------------------------------------------------------------------ ledger
function renderLedger() {
  const user = store.session?.user;
  const box = $("#deck");
  $("#ledgerSub").textContent = user
    ? "Every deck you've fed, newest first. Click a card to load it into the machine."
    : "Punch in and every deck you feed lands here, scored and stamped.";
  if (!user) {
    box.replaceChildren(h("button", { type: "button", class: "ledger-card is-new", onclick: () => emit("go", "#feed") }, h("b", { text: "+" }), h("span", { text: "Punch in to start a ledger" })));
    renderDock();
    return;
  }
  const cards = store.runs.map((r, i) => {
    const s = r.summary || {};
    const active = ["queued", "running"].includes(r.status);
    const good = r.status === "done" && s.units && s.verified === s.units && s.scenarios_identical === s.scenarios;
    return h("div", {
      role: "button", tabindex: 0, class: "ledger-card" + (store.run?.id === r.id ? " is-current" : ""),
      style: { "--rot": `${(i % 2 ? 1 : -1) * (2 + (i % 3) * 1.5)}deg`, "--ty": `${(i % 3) * 8}px`, zIndex: 100 - i },
      onclick: async () => { await loadRun(r.id); emit("go", "#map"); },
      onkeydown: async (e) => { if (e.key === "Enter") { await loadRun(r.id); emit("go", "#map"); } },
    },
    ledgerHoles(r.id),
    h("h4", { text: r.title }),
    h("div", { class: "lc-meta", text: `${r.id} · ${fmtAgo(r.created_at)}\n${r.dry_run ? "offline mock" : "free-tier LLMs"}${r.fuzz ? " · fuzz" : ""}` }),
    h("div", { class: "lc-score" }, h("b", { text: active ? "…" : s.units ? `${s.verified}/${s.units}` : "–" }), h("span", { text: active ? r.status : "units verified" })),
    h("span", { class: `stamp ${good ? "ok" : "bad"}`, text: active ? r.status : r.status === "failed" ? "failed" : good ? "proven" : "partial" }),
    active ? null : h("button", {
      type: "button", class: "lc-del", "aria-label": `Delete run ${r.id}`, text: "×",
      onclick: async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete run ${r.id}? Its package and report go too.`)) return;
        try { await api(`/api/runs/${r.id}`, { method: "DELETE" }); await refreshRuns(); renderLedger(); if (store.run?.id === r.id) loadRun(store.session.specimen); }
        catch (ex) { toast(ex.message, { tag: "ledger", bad: true }); }
      },
    }));
  });
  const fresh = h("button", { type: "button", class: "ledger-card is-new", onclick: () => emit("go", "#feed") }, h("b", { text: "+" }), h("span", { text: "Feed a new deck" }));
  box.replaceChildren(fresh, ...cards);
  if (!store.runs.length) box.append(h("p", { class: "empty-note", text: "No runs yet. The specimen is showing until you feed one." }));
  renderDock();
}

function wireRunSwitch() {
  $("#runSwitch").addEventListener("click", () => emit("palette", "runs"));
  $("#replayBtn").addEventListener("click", replay);
}

export function paletteRuns() {
  const items = [{ label: "Specimen · bank_demo", hint: "public", run: () => loadRun(store.session.specimen) }];
  for (const r of store.runs) items.push({ label: `${r.title} · ${r.id}`, hint: `${r.status} · ${fmtAgo(r.created_at)}`, run: () => loadRun(r.id) });
  return items;
}
