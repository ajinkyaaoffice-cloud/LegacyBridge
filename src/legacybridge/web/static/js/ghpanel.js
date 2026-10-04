// The GitHub source in the hopper: connect (OAuth through Supabase, or a pasted token), list your
// repos, clone the COBOL-relevant files into this browser (IndexedDB), re-sync when the branch
// moves, pick files and load them into the deck. Nothing here goes through the LegacyBridge server.

import { $, fmtAgo, h, toast } from "./api.js";
import * as G from "./github.js";
import { addToDeck } from "./product.js";
import { enabled as supabaseOn, signInWithGitHub } from "./supa.js";

let current = null;        // the cached record being browsed
let picked = new Set();
let reposLoaded = false;

export function initGitHubPanel() {
  $("#ghRepoForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const full = G.parseRepo($("#ghRepo").value);
    if (!full) return toast("Type owner/repo or paste a GitHub URL.", { tag: "github", bad: true });
    clone(full);
  });
  $("#ghTokenForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const t = $("#ghToken").value.trim();
    if (!t) return;
    G.setToken(t);
    $("#ghToken").value = "";
    $(".gh-pat").open = false;
    reposLoaded = false;
    await refreshWho();
  });
  $("#ghConnect").addEventListener("click", () => signInWithGitHub().catch((e) => toast(e.message, { tag: "github", bad: true })));
  $("#ghDisconnect").addEventListener("click", () => { G.setToken(""); reposLoaded = false; $("#ghReposWrap").hidden = true; refreshWho(); toast("Token forgotten. Cloned repos stay cached in this browser.", { tag: "github" }); });
  $("#ghAll").addEventListener("click", () => { picked = new Set(current?.files.map((f) => f.path) || []); renderTree(); });
  $("#ghNone").addEventListener("click", () => { picked = new Set(); renderTree(); });
  $("#ghLoad").addEventListener("click", loadPicked);
}

/** Called when the GitHub tab opens (and after an OAuth return). */
export async function showGitHub() {
  await Promise.all([refreshWho(), renderCached()]);
}

async function refreshWho() {
  const token = G.getToken();
  $("#ghConnect").hidden = !!token || !supabaseOn();
  $("#ghDisconnect").hidden = !token;
  $("#ghWho").classList.toggle("is-on", !!token);
  if (!token) {
    $("#ghWhoText").textContent = supabaseOn() ? "Not connected. Public repos work without it." : "No token. Public repos work; paste a token for private ones.";
    return;
  }
  try {
    const me = await G.me();
    $("#ghWhoText").textContent = `Connected as ${me.login}`;
    if (!reposLoaded) await renderRepos();
  } catch (e) {
    $("#ghWhoText").textContent = e.message;
    $("#ghWho").classList.remove("is-on");
  }
}

async function renderRepos() {
  try {
    const repos = await G.listRepos();
    reposLoaded = true;
    $("#ghReposWrap").hidden = !repos.length;
    $("#ghRepos").replaceChildren(...repos.map((r) => h("li", {},
      h("button", { type: "button", onclick: () => clone(r.full, r.branch) },
        h("b", { text: r.full }),
        h("small", { class: "mono", text: [r.private ? "private" : "public", r.lang, r.updated && fmtAgo(Date.parse(r.updated) / 1000)].filter(Boolean).join(" · ") })))));
  } catch (e) { toast(e.message, { tag: "github", bad: true }); }
}

async function renderCached() {
  const list = await G.cached();
  const box = $("#ghCached");
  if (!list.length) { box.replaceChildren(h("li", { class: "gh-empty mono", text: "Nothing cloned yet." })); return; }
  box.replaceChildren(...list.map((r) => h("li", { class: current?.full === r.full ? "is-on" : "" },
    h("button", { type: "button", onclick: () => open(r) },
      h("b", { text: r.full }),
      h("small", { class: "mono", text: `${r.files.length} files · ${r.branch}@${r.sha.slice(0, 7)} · ${fmtAgo(r.fetched_at / 1000)}` })),
    h("span", { class: "gh-acts" },
      h("button", { type: "button", class: "rb-btn", title: "Pull again if the branch moved", text: "Sync", onclick: () => resync(r) }),
      h("button", { type: "button", class: "rb-btn", "aria-label": `Forget ${r.full}`, text: "×", onclick: async () => { await G.forget(r.full); if (current?.full === r.full) { current = null; $("#ghFiles").hidden = true; } renderCached(); } })))));
}

function progress(on, text = "", frac = 0) {
  const p = $("#ghProgress");
  p.hidden = !on;
  p.querySelector("span").textContent = text;
  p.querySelector("i").style.transform = `scaleX(${frac})`;
}

async function clone(full, branch = null) {
  $("#ghClone").disabled = true;
  progress(true, `Reading ${full}…`, .04);
  try {
    const rec = await G.cloneRepo(full, { branch, onProgress: ({ done, total, path }) => progress(true, `${done}/${total} · ${path}`, done / total) });
    progress(true, `Cloned ${rec.files.length} files into this browser.`, 1);
    setTimeout(() => progress(false), 1400);
    const notes = [];
    if (rec.skipped.big) notes.push(`${rec.skipped.big} file(s) over 512 KB skipped`);
    if (rec.skipped.more) notes.push(`${rec.skipped.more} more beyond the ${G.MAX_FILES}-file limit`);
    if (rec.truncated) notes.push("GitHub truncated the tree (very large repo)");
    if (notes.length) toast(notes.join("; ") + ".", { tag: "github" });
    $("#ghRepo").value = "";
    await renderCached();
    open(rec);
  } catch (e) {
    progress(false);
    toast(e.message, { tag: "github", bad: true, ms: 7000 });
  } finally {
    $("#ghClone").disabled = false;
  }
}

async function resync(rec) {
  try {
    const sha = await G.head(rec.full, rec.branch);
    if (sha === rec.sha) { toast(`${rec.full} is up to date (${sha.slice(0, 7)}).`, { tag: "github" }); return; }
    await clone(rec.full, rec.branch);
  } catch (e) { toast(e.message, { tag: "github", bad: true }); }
}

function open(rec) {
  current = rec;
  picked = G.defaultPick(rec.files);
  $("#ghFiles").hidden = false;
  $("#ghFilesTitle").textContent = rec.full;
  renderTree();
  renderCached();
  if (window.gsap) window.gsap.from("#ghFiles", { y: 24, opacity: 0, duration: .5, ease: "expo.out", clearProps: "transform,opacity" });
}

function renderTree() {
  if (!current) return;
  const tree = $("#ghTree");
  tree.replaceChildren(...current.files.map((f) => {
    const id = "gh-" + f.sha + f.path.length;
    const box = h("input", { type: "checkbox", id, checked: picked.has(f.path) || null });
    box.addEventListener("change", () => { box.checked ? picked.add(f.path) : picked.delete(f.path); meta(); });
    return h("li", { class: `k-${G.kindOf(f.path)}` }, box,
      h("label", { for: id }, h("span", { text: f.path }), h("small", { class: "mono", text: `${G.kindOf(f.path)} · ${(f.size / 1024).toFixed(1)} KB` })));
  }));
  meta();
}

function meta() {
  const n = picked.size;
  $("#ghFilesMeta").textContent = `${n} of ${current.files.length} picked · ${current.branch}@${current.sha.slice(0, 7)}`;
  $("#ghLoad").disabled = !n;
}

function loadPicked() {
  if (!current) return;
  const files = current.files.filter((f) => picked.has(f.path));
  const seen = new Map(), clash = [];
  for (const f of files) {
    const name = G.baseName(f.path);
    if (seen.has(name.toLowerCase())) clash.push(f.path); else seen.set(name.toLowerCase(), { name, content: f.content, size: f.size, from: `${current.full}/${f.path}` });
  }
  if (clash.length) toast(`Same file name twice, kept the first: ${clash.join(", ")}`, { tag: "github", bad: true, ms: 7000 });
  addToDeck([...seen.values()], { title: current.full.split("/")[1], replace: true });
  toast(`${seen.size} cards from ${current.full} are in the hopper.`, { tag: "github" });
}
