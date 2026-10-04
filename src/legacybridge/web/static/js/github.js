// GitHub, straight from the browser: list your repos, "clone" the COBOL-relevant files of one into
// IndexedDB (this browser's cache), re-sync when the branch moves, feed cached files to the machine.
// The token (from "Continue with GitHub" or a pasted PAT) lives in this tab's sessionStorage and is
// only ever sent to api.github.com, never to the LegacyBridge server.

const API = "https://api.github.com";
const TOKEN_KEY = "lb.gh.token";
const DB = "legacybridge", STORE = "repos";
export const WANT = /\.(cbl|cob|cobol|cpy|copy|txt)$/i;
export const MAX_FILES = 80, MAX_FILE = 512 * 1024, MAX_TOTAL = 2 * 1024 * 1024;

// ------------------------------------------------------------------ token
export function getToken() { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } }
export function setToken(t) { try { t ? sessionStorage.setItem(TOKEN_KEY, t.trim()) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } }

async function gh(path, { raw = false, token = getToken() } = {}) {
  const headers = { Accept: raw ? "application/vnd.github.raw+json" : "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(API + path, { headers });
  if (!res.ok) {
    let msg = `GitHub said ${res.status}`;
    if (res.status === 401) msg = "GitHub rejected the token. Connect again or paste a new one.";
    else if (res.status === 403 && res.headers.get("x-ratelimit-remaining") === "0") msg = "GitHub rate limit reached. Connect GitHub (or add a token) for a higher limit.";
    else if (res.status === 404) msg = "Repo not found (or private: connect GitHub with repo access).";
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return raw ? res.text() : res.json();
}

export const me = () => gh("/user");
export async function listRepos() {
  const repos = await gh("/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member");
  return repos.map((r) => ({ full: r.full_name, private: r.private, branch: r.default_branch, updated: r.pushed_at, lang: r.language, desc: r.description }));
}

/** "owner/repo", "https://github.com/owner/repo(.git)", "git@github.com:owner/repo.git" -> "owner/repo" */
export function parseRepo(input) {
  const s = String(input || "").trim().replace(/\.git$/, "").replace(/\/+$/, "");
  const m = s.match(/(?:github\.com[/:])?([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/);
  return m ? `${m[1]}/${m[2]}` : null;
}

export async function head(full, branch) {
  const b = await gh(`/repos/${full}/branches/${encodeURIComponent(branch)}`);
  return b.commit.sha;
}

/**
 * Fetch the COBOL-relevant files of a repo at its default (or given) branch into the cache.
 * onProgress({done, total, path}). Returns the cached record.
 */
export async function cloneRepo(full, { branch = null, onProgress = () => {} } = {}) {
  const info = await gh(`/repos/${full}`);
  branch = branch || info.default_branch;
  const sha = await head(full, branch);
  const tree = await gh(`/repos/${full}/git/trees/${sha}?recursive=1`);
  let blobs = tree.tree.filter((t) => t.type === "blob" && WANT.test(t.path) && t.size <= MAX_FILE);
  const skipped = { big: tree.tree.filter((t) => t.type === "blob" && WANT.test(t.path) && t.size > MAX_FILE).length, more: 0 };
  // programs and copybooks first, then scenario text
  blobs.sort((a, b) => (/\.txt$/i.test(a.path) - /\.txt$/i.test(b.path)) || a.path.localeCompare(b.path));
  let total = 0;
  blobs = blobs.filter((b) => { if (total + b.size > MAX_TOTAL) return false; total += b.size; return true; });
  if (blobs.length > MAX_FILES) { skipped.more = blobs.length - MAX_FILES; blobs = blobs.slice(0, MAX_FILES); }
  if (!blobs.length) throw new Error("No .cbl/.cob/.cpy/.txt files in that repo.");
  const files = [];
  let done = 0;
  const queue = [...blobs];
  const worker = async () => {
    while (queue.length) {
      const b = queue.shift();
      const content = await gh(`/repos/${full}/git/blobs/${b.sha}`, { raw: true });
      files.push({ path: b.path, content, size: b.size, sha: b.sha });
      onProgress({ done: ++done, total: blobs.length, path: b.path });
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  files.sort((a, b) => a.path.localeCompare(b.path));
  const rec = { full, branch, sha, private: info.private, fetched_at: Date.now(), files, skipped, truncated: !!tree.truncated };
  await put(rec);
  return rec;
}

// ------------------------------------------------------------------ IndexedDB cache
function openDb() {
  return new Promise((ok, bad) => {
    if (!window.indexedDB) return bad(new Error("This browser has no IndexedDB."));
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "full" });
    req.onsuccess = () => ok(req.result);
    req.onerror = () => bad(req.error || new Error("IndexedDB unavailable"));
  });
}
async function tx(mode, fn) {
  const db = await openDb();
  return new Promise((ok, bad) => {
    const t = db.transaction(STORE, mode);
    const out = fn(t.objectStore(STORE));
    t.oncomplete = () => { db.close(); ok(out?.result ?? out); };
    t.onerror = () => { db.close(); bad(t.error); };
  });
}
export const put = (rec) => tx("readwrite", (s) => s.put(rec));
export const get = (full) => tx("readonly", (s) => s.get(full));
export const forget = (full) => tx("readwrite", (s) => s.delete(full));
export async function cached() {
  try {
    const all = await tx("readonly", (s) => s.getAll());
    return (all || []).sort((a, b) => b.fetched_at - a.fetched_at);
  } catch { return []; }
}

// ------------------------------------------------------------------ deck helpers
export const baseName = (p) => p.split("/").pop();
export function kindOf(name) {
  if (/\.(cpy|copy)$/i.test(name)) return "copybook";
  if (/\.txt$/i.test(name)) return "scenario";
  return "program";
}

/** Which files to tick by default: programs, copybooks, and .txt that look like <PROGRAM>-<name>.txt */
export function defaultPick(files) {
  const programs = new Set(files.filter((f) => kindOf(f.path) === "program").map((f) => baseName(f.path).replace(/\.[^.]+$/, "").toUpperCase()));
  return new Set(files.filter((f) => {
    const k = kindOf(f.path);
    if (k !== "scenario") return true;
    const stem = baseName(f.path).replace(/\.txt$/i, "");
    return programs.has(stem.split("-")[0].toUpperCase());
  }).map((f) => f.path));
}
