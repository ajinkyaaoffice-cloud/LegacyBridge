// LegacyBridge dashboard: polls /api/state on relevant events, streams /api/stream (SSE).
"use strict";

const COLORS = {
  pending: "--idle", oracle: "--accent", ready: "--accent", translating: "--work", verifying: "--work",
  repairing: "--warn", fuzzing: "--work", verified: "--ok", blocked: "--bad", dep_blocked: "--bad",
};
const ORDER = ["verified", "fuzzing", "repairing", "verifying", "translating", "ready", "oracle", "pending",
               "blocked", "dep_blocked"];
const REFRESH_TYPES = new Set(["status", "verify", "oracle", "fuzz_round", "fuzz_bug", "lesson_added",
  "run_started", "run_finished", "resume", "analyzed", "integrated", "scenario", "unit_crashed"]);

let state = null, cy = null, selected = null, lastId = 0, refreshTimer = null, detailTimer = null;
const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const color = (s) => css(COLORS[s] || "--idle");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// ------------------------------------------------------------------ overview
async function refreshState() {
  try { state = await getJSON("/api/state"); } catch (e) { return; }
  $("source").textContent = state.source_root || "(no run yet)";
  const v = state.counts.verified || 0, total = state.total || 0;
  $("bar").style.width = total ? `${(100 * v) / total}%` : "0";
  $("progress-text").textContent = total ? `${v}/${total} verified` : "no units";
  $("counts").innerHTML = ORDER.filter((s) => state.counts[s])
    .map((s) => `<span class="pill"><i style="background:${color(s)}"></i>${s.replace("_", "-")} ${state.counts[s]}</span>`).join("");
  $("n-lessons").textContent = state.lessons.length ? `(${state.lessons.length})` : "";
  drawGraph();
  renderLessons();
  renderLLM();
  renderTests();
}

function waves(units, edges) {
  // longest path from the leaves: dependencies end up in lower waves (drawn on the left)
  const deps = {}; units.forEach((u) => (deps[u.id] = []));
  edges.forEach((e) => deps[e.source] && deps[e.source].push(e.target));
  const memo = {};
  const depth = (id, seen = new Set()) => {
    if (id in memo) return memo[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const d = deps[id].length ? 1 + Math.max(...deps[id].map((x) => depth(x, seen))) : 0;
    return (memo[id] = d);
  };
  units.forEach((u) => depth(u.id));
  return memo;
}

function drawGraph() {
  const showCopy = $("show-copy").checked;
  const units = state.units.filter((u) => showCopy || u.kind !== "copybook");
  const ids = new Set(units.map((u) => u.id));
  const edges = state.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  if (typeof cytoscape === "undefined") return drawFallback(units);
  const w = waves(units, edges);
  const cols = {};
  units.forEach((u) => (cols[w[u.id]] = cols[w[u.id]] || []).push(u));
  const pos = {};
  Object.entries(cols).forEach(([col, us]) => {
    us.sort((a, b) => (a.kind === "copybook") - (b.kind === "copybook") || a.id.localeCompare(b.id));
    us.forEach((u, i) => (pos[u.id] = { x: 210 * Number(col), y: 78 * (i - (us.length - 1) / 2) }));
  });
  const label = (u) => {
    if (u.kind === "copybook") return u.id;
    const extra = u.status === "verified" ? `✓ ${u.attempts} att` : u.attempts ? `${u.status} · ${u.attempts} att` : u.status;
    return `${u.id}\n${extra.replace("_", "-")}`;
  };
  const elements = [
    ...units.map((u) => ({ data: { id: u.id, label: label(u), kind: u.kind, status: u.status, bugs: u.fuzz_bugs_found },
                           position: pos[u.id] })),
    ...edges.map((e) => ({ data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target, kind: e.kind } })),
  ];
  const sig = JSON.stringify(elements.map((e) => [e.data.id, e.data.label, e.data.status]));
  if (cy && cy.scratch("sig") === sig) return;
  const fresh = !cy || cy.scratch("ids") !== JSON.stringify([...ids].sort());
  if (!cy) {
    cy = cytoscape({ container: $("graph"), wheelSensitivity: 0.25, minZoom: 0.3, maxZoom: 2.5 });
    cy.on("tap", "node", (ev) => selectUnit(ev.target.id()));
  }
  cy.style(graphStyle());
  cy.json({ elements: { nodes: [], edges: [] } });
  cy.add(elements);
  cy.layout({ name: "preset" }).run();
  if (fresh) cy.fit(undefined, 40);
  if (selected && cy.getElementById(selected).length) cy.getElementById(selected).select();
  cy.scratch("sig", sig);
  cy.scratch("ids", JSON.stringify([...ids].sort()));
  $("legend").innerHTML = ["pending", "translating", "repairing", "fuzzing", "verified", "blocked"]
    .map((s) => `<span><i style="background:${color(s)}"></i>${s}</span>`).join("") +
    `<span>▭ program · ◯ subprogram · ◇ copybook · ⟶ calls · ┄ copies</span>`;
}

function graphStyle() {
  return [
    { selector: "node", style: {
        label: "data(label)", "text-wrap": "wrap", "text-valign": "center", "text-halign": "center",
        "font-size": 11, "font-family": "ui-monospace, Menlo, monospace", color: css("--text"),
        "background-color": css("--panel-2"), "border-width": 2.5, "border-color": (n) => color(n.data("status")),
        width: 150, height: 46, shape: "round-rectangle" } },
    { selector: 'node[kind = "subprogram"]', style: { shape: "ellipse" } },
    { selector: 'node[kind = "copybook"]', style: { shape: "diamond", width: 90, height: 40, "font-size": 9,
        "border-width": 1, "border-color": css("--idle"), color: css("--muted") } },
    { selector: 'node[status = "verified"]', style: { "background-color": `color-mix(in srgb, ${css("--ok")} 14%, ${css("--panel-2")})` } },
    { selector: 'node[status = "blocked"], node[status = "dep_blocked"]', style: { "background-color": `color-mix(in srgb, ${css("--bad")} 14%, ${css("--panel-2")})` } },
    { selector: 'node[bugs > 0]', style: { "border-style": "double", "border-width": 5 } },
    { selector: "node:selected", style: { "overlay-color": css("--accent"), "overlay-opacity": 0.18, "overlay-padding": 6 } },
    { selector: "edge", style: { width: 1.5, "line-color": css("--line"), "target-arrow-color": css("--line"),
        "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.9 } },
    { selector: 'edge[kind = "copy"]', style: { "line-style": "dashed", "target-arrow-shape": "none" } },
  ];
}

function drawFallback(units) {
  $("graph").innerHTML = `<div class="graph-fallback">Graph library unavailable offline; units:<ul>${
    units.map((u) => `<li data-u="${esc(u.id)}"><span class="pill"><i style="background:${color(u.status)}"></i>${esc(u.status)}</span> ${esc(u.id)}</li>`).join("")}</ul></div>`;
  $("graph").querySelectorAll("li").forEach((li) => (li.onclick = () => selectUnit(li.dataset.u)));
}

// ------------------------------------------------------------------ unit detail
async function selectUnit(name) {
  selected = name;
  if (cy) { cy.$(":selected").unselect(); cy.getElementById(name).select(); }
  if ($("filter-unit").checked) renderEventFilter();
  await renderDetail();
}

async function renderDetail() {
  if (!selected) return;
  let u;
  try { u = await getJSON(`/api/unit/${encodeURIComponent(selected)}`); } catch (e) { return; }
  const exec = u.members.filter((m) => m.kind !== "copybook");
  const tests = {};
  exec.forEach((m) => Object.entries(m.tests).forEach(([o, n]) => (tests[o] = (tests[o] || 0) + n)));
  const testStr = Object.entries(tests).sort().map(([o, n]) => `${o} ${n}`).join(" · ") || "none yet";
  const openIdx = u.attempts.length - 1;
  const prevOpen = new Set([...document.querySelectorAll(".attempts details[open]")].map((d) => d.dataset.id));
  $("detail").innerHTML = `
    <div class="unit-title"><h2>${esc(u.name)}</h2>
      <span class="status" style="background:${color(u.status)}">${esc(u.status.replace("_", "-"))}</span></div>
    <dl class="kv">
      <dt>members</dt><dd class="mono">${u.members.map((m) => `${esc(m.name)} <span class="hint">(${esc(m.kind)})</span>`).join(", ")}</dd>
      <dt>depends on</dt><dd class="mono">${esc(u.deps.join(", ") || "–")}</dd>
      <dt>golden tests</dt><dd>${esc(testStr)}</dd>
      <dt>attempts</dt><dd>${u.attempts.length}${u.model_used ? ` · final model <code>${esc(u.model_used)}</code>` : ""}</dd>
      <dt>fuzzing</dt><dd>${u.fuzz_rounds_clean} clean round(s) · ${u.fuzz_bugs_found} counterexample(s) found</dd>
      ${exec.length ? `<dt>features</dt><dd class="tags">${[...new Set(exec.flatMap((m) => m.features))].map((f) => `<span>${esc(f)}</span>`).join("")}</dd>` : ""}
    </dl>
    ${u.error ? `<div class="error">${esc(u.error)}</div>` : ""}
    ${exec.map((m) => m.summary ? `<h3>${esc(m.name)} — summary</h3><div>${esc(m.summary)}</div>` : "").join("")}
    <h3>Translation attempts</h3>
    ${u.attempts.length ? `<ol class="attempts">${u.attempts.map((a, i) => attemptHTML(a, prevOpen.size ? prevOpen.has(String(a.id)) : i === openIdx)).join("")}</ol>`
      : `<div class="hint">${exec.length ? "not translated yet" : "data-only unit (copybook): nothing to translate"}</div>`}
    ${exec.length ? `<h3>Code</h3><div class="codetabs" id="codetabs"></div><pre id="codeview"></pre>` : ""}
    ${exec.length && exec[0].sample_tests.length ? `<h3>Sample golden test (${esc(exec[0].name)})</h3>${sampleHTML(exec[0].sample_tests[0])}` : ""}`;
  if (exec.length) codeTabs(exec, u.attempts);
}

function attemptHTML(a, open) {
  const res = Object.entries(a.diff || {}).filter(([, s]) => s && typeof s === "object");
  const score = res.map(([m, s]) => `${m} ${s.passed ?? "?"}/${s.total ?? "?"}`).join(", ");
  const fails = res.filter(([, s]) => s.first_failure || s.static_error);
  return `<li><details data-id="${a.id}" ${open ? "open" : ""}><summary>
      <b>#${a.attempt_no}</b> ${esc(a.kind)} <span class="hint">${esc(a.stage || "")}</span>
      <span class="verdict ${esc(a.verdict || "")}">${esc(a.verdict || "running")}</span>
      <span class="hint">${esc(score)}</span><span class="model">${esc(a.model || "")}</span></summary>
      <div class="body">
      ${a.lessons && a.lessons.length ? `<div class="hint">prompt carried lessons #${a.lessons.map(esc).join(", #")}</div>` : ""}
      ${fails.map(([m, s]) => failureHTML(m, s)).join("") || (a.verdict === "pass" ? `<div class="hint">all tests identical to the original</div>` : "")}
      </div></details></li>`;
}

function failureHTML(m, s) {
  if (s.static_error) return `<div class="error">${esc(m)}: ${esc(s.static_error)}</div>`;
  const f = s.first_failure || {};
  const d = f;
  let out = `<div class="hint">${esc(m)}: first failing test ${esc(f.name || "")}</div>`;
  if (f.input) out += `<pre>${esc(String(f.input).slice(0, 1500))}</pre>`;
  if (d.expected !== undefined || d.actual !== undefined) {
    // make trailing whitespace visible: it is part of the output and a common divergence
    const vis = (s) => typeof s === "string" ? s.replace(/ +$/, (m) => "·".repeat(m.length)) : JSON.stringify(s);
    const len = (s) => typeof s === "string" ? ` (${s.length})` : "";
    out += `<pre class="diff"><span class="exp">expected: ${esc(vis(d.expected))}${len(d.expected)}</span>\n<span class="act">actual:   ${esc(vis(d.actual))}${len(d.actual)}</span>${d.line ? `\n(line ${esc(d.line)}; · = trailing space)` : ""}</pre>`;
  }
  if (d.fields) out += `<pre>${esc(d.fields)}</pre>`;
  if (d.stderr_tail) out += `<pre>${esc(d.stderr_tail)}</pre>`;
  return out;
}

function sampleHTML(t) {
  return `<div class="hint">origin ${esc(t.origin)} · ${esc(t.name || "")} · exit ${esc(t.expected_exit)}</div>
    <pre>stdin:\n${esc(t.input)}</pre><pre>expected stdout (from the original program):\n${esc(t.expected_stdout)}</pre>`;
}

function codeTabs(exec, attempts) {
  const views = [];
  exec.forEach((m) => {
    views.push({ label: `${m.name}.cbl`, text: m.source });
    if (m.python_code) views.push({ label: `${m.name} ✓ python`, text: m.python_code });
  });
  const last = attempts[attempts.length - 1];
  if (last && !exec.every((m) => m.python_code)) {
    Object.entries(last.code || {}).forEach(([t, code]) => views.push({ label: `${t}.py (attempt #${last.attempt_no})`, text: code }));
  }
  const tabs = $("codetabs"), view = $("codeview");
  tabs.innerHTML = views.map((v, i) => `<button data-i="${i}">${esc(v.label)}</button>`).join("");
  const show = (i) => {
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.i === String(i)));
    view.textContent = views[i] ? views[i].text || "" : "";
  };
  tabs.querySelectorAll("button").forEach((b) => (b.onclick = () => show(Number(b.dataset.i))));
  const py = views.findIndex((v) => v.label.includes("python") || v.label.includes(".py"));
  show(py >= 0 ? py : 0);
}

// ------------------------------------------------------------------ bottom tabs
function renderLessons() {
  $("tab-lessons").innerHTML = state.lessons.length ? `<table><tr><th>#</th><th>lesson</th><th>tags</th><th>from</th><th class="num">used</th></tr>${
    state.lessons.map((l) => `<tr><td>${l.id}</td><td>${esc(l.text)}</td><td class="tags">${l.tags.map((t) => `<span>${esc(t)}</span>`).join("")}</td>
      <td class="mono">${esc(l.source_unit)}</td><td class="num">${l.uses}</td></tr>`).join("")}</table>`
    : `<p class="hint">No lessons yet. The reflector writes one when a unit needed a repair to pass.</p>`;
}

function renderLLM() {
  const rows = state.llm;
  $("tab-llm").innerHTML = rows.length ? `<table><tr><th>provider</th><th>model</th><th>status</th><th class="num">calls</th>
    <th class="num">cached</th><th class="num">prompt tok</th><th class="num">completion tok</th><th class="num">avg ms</th></tr>${
    rows.map((r) => `<tr><td>${esc(r.provider)}</td><td class="mono">${esc(r.model)}</td><td>${esc(r.status)}</td>
      <td class="num">${r.n}</td><td class="num">${r.cached || 0}</td><td class="num">${(r.pt || 0).toLocaleString()}</td>
      <td class="num">${(r.ct || 0).toLocaleString()}</td><td class="num">${Math.round(r.lat || 0)}</td></tr>`).join("")}</table>`
    : `<p class="hint">No LLM calls recorded.</p>`;
}

function renderTests() {
  const t = Object.entries(state.tests).sort();
  const sc = state.scenarios || [];
  $("tab-tests").innerHTML = `<h3>Golden tests by origin</h3>${t.length ? `<table><tr>${t.map(([o]) => `<th class="num">${esc(o)}</th>`).join("")}</tr>
    <tr>${t.map(([, n]) => `<td class="num">${n}</td>`).join("")}</tr></table>` : `<p class="hint">none yet</p>`}
    <h3>End-to-end scenarios (original vs packaged Python)</h3>${sc.length ? `<table><tr><th>scenario</th><th>program</th><th>result</th><th class="num">lines</th><th>note</th></tr>${
      sc.map((s) => `<tr><td>${esc(s.name)}</td><td class="mono">${esc(s.module)}</td>
        <td style="color:${s.passed === true ? css("--ok") : s.passed === false ? css("--bad") : css("--muted")}">${s.passed === true ? "IDENTICAL" : s.passed === false ? "DIFFERS" : "skipped"}</td>
        <td class="num">${s.lines}</td><td>${esc(s.note || "")}</td></tr>`).join("")}</table>`
      : `<p class="hint">Run <code>legacybridge migrate … --out DIR</code> or <code>legacybridge report</code> to replay scenarios.</p>`}`;
}

// ------------------------------------------------------------------ events
function describe(ev) {
  const p = ev;
  switch (ev.type) {
    case "status": return [p.status === "verified" ? "ok" : p.status.includes("blocked") ? "bad" : "work",
      `${p.status}${p.attempts && p.status !== "verified" ? ` (attempt ${p.attempts})` : ""}${p.error ? ` — ${p.error}` : ""}`];
    case "verify": return [p.ok ? "ok" : "warn", `verify ${p.passed}/${p.total} ${p.ok ? "PASS" : "fail"} · ${p.role || ""} ${p.model || ""}`];
    case "oracle": return ["dim", `oracle ${p.module}: ${p.total} golden tests from the original`];
    case "fuzz_round": return ["", `fuzz round ${p.round}: ${p.new_tests} new tests, no divergence (${p.clean}/${p.needed} clean)`];
    case "fuzz_bug": return ["bad", `fuzz round ${p.round}: ${p.counterexamples} counterexample(s) in ${(p.modules || []).join(", ")} → repair`];
    case "lesson_added": return ["work", `lesson #${p.lesson_id}: ${p.text}`];
    case "llm_call": return [p.status === "ok" ? "dim" : "warn", `llm ${p.role} → ${p.model} ${p.status}${p.cache_hit ? " (cache)" : ""}`];
    case "run_started": return ["", `run started (concurrency ${p.concurrency ?? "?"})`];
    case "run_finished": return ["ok", `run finished in ${p.seconds}s`];
    case "scenario": return [p.passed ? "ok" : "bad", `scenario ${p.module}/${p.name}: ${p.passed ? "IDENTICAL" : "DIFFERS"}`];
    case "integrated": return ["ok", `package written: ${p.package} (${p.scenarios_passed}/${p.scenarios_total} scenarios identical)`];
    default: {
      const rest = Object.fromEntries(Object.entries(p).filter(([k]) => !["id", "ts", "type", "unit"].includes(k)));
      const bad = /fail|crash|error|rejected|dropped/.test(ev.type);
      return [bad ? "warn" : "dim", `${ev.type} ${JSON.stringify(rest).slice(0, 220)}`];
    }
  }
}

function addEvent(ev) {
  const [cls, text] = describe(ev);
  const li = document.createElement("li");
  li.dataset.unit = ev.unit || "";
  li.innerHTML = `<span class="t">${fmtTime(ev.ts)}</span><span class="u">${esc(ev.unit || "")}</span><span class="${cls}">${esc(text)}</span>`;
  if (ev.unit) li.querySelector(".u").onclick = () => selectUnit(ev.unit);
  if ($("filter-unit").checked && selected && ev.unit !== selected) li.hidden = true;
  const list = $("events");
  const stick = list.parentElement.scrollTop + list.parentElement.clientHeight >= list.parentElement.scrollHeight - 30;
  list.appendChild(li);
  while (list.children.length > 1500) list.removeChild(list.firstChild);
  if (stick) list.parentElement.scrollTop = list.parentElement.scrollHeight;
}

function renderEventFilter() {
  const only = $("filter-unit").checked && selected;
  $("events").querySelectorAll("li").forEach((li) => (li.hidden = only ? li.dataset.unit !== selected : false));
}

function connect() {
  const es = new EventSource(`/api/stream?after=${lastId}`);
  es.onopen = () => { $("conn").className = "conn live"; $("conn").querySelector("span").textContent = "live"; };
  es.onerror = () => {
    $("conn").className = "conn down"; $("conn").querySelector("span").textContent = "reconnecting";
    es.close(); setTimeout(connect, 2000);
  };
  es.addEventListener("reset", () => {
    lastId = 0; $("events").innerHTML = ""; selected = null;
    $("detail").innerHTML = `<div class="empty">A new run started. Select a unit.</div>`;
    if (cy) cy.scratch("sig", null);
    refreshState();
  });
  es.addEventListener("ev", (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.id <= lastId) return;
    lastId = ev.id;
    addEvent(ev);
    if (REFRESH_TYPES.has(ev.type)) {
      clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshState, 250);
      if (selected && (ev.unit === selected || !ev.unit)) { clearTimeout(detailTimer); detailTimer = setTimeout(renderDetail, 300); }
    }
  });
}

// ------------------------------------------------------------------ boot
document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab-body").forEach((x) => x.classList.toggle("active", x.id === `tab-${b.dataset.tab}`));
}));
$("show-copy").onchange = () => { if (cy) cy.scratch("sig", null); drawGraph(); };
$("filter-unit").onchange = renderEventFilter;
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (cy) { cy.resize(); cy.fit(undefined, 40); } }, 150);
});
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { if (cy) { cy.scratch("sig", null); drawGraph(); } });

(async () => {
  const backlog = await getJSON("/api/events?after=0&limit=5000").catch(() => []);
  const recent = backlog.slice(-400);
  recent.forEach(addEvent);
  lastId = backlog.length ? backlog[backlog.length - 1].id : 0;
  await refreshState();
  connect();
})();
