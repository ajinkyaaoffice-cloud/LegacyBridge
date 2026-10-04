// Optional Supabase Auth in the browser (only when the server exposes SUPABASE_URL + anon key).
// A Supabase session is exchanged once for the site's own session cookie (/api/auth/supabase);
// after a GitHub sign-in the provider token is kept for repo access (this tab only).

import { api, store } from "./api.js";
import { setToken } from "./github.js";

let client = null, loading = null;

export const enabled = () => !!store.session?.supabase;

export async function supabase() {
  if (!enabled()) return null;
  if (client) return client;
  loading ||= import("https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm").then((m) => {
    const cfg = store.session.supabase;
    client = m.createClient(cfg.url, cfg.anon_key, { auth: { persistSession: true, detectSessionInUrl: true, flowType: "pkce" } });
    return client;
  });
  return loading;
}

async function exchange(session) {
  const res = await api("/api/auth/supabase", { method: "POST", body: { access_token: session.access_token } });
  return res.user;
}

/**
 * Wire Supabase: picks up a session coming back from GitHub OAuth (or a stored one) and turns it
 * into a site session. onSignedIn(user) runs when that happens.
 */
export async function initSupabase({ onSignedIn }) {
  const sb = await supabase().catch(() => null);
  if (!sb) return;
  const handle = async (session) => {
    if (!session) return;
    if (session.provider_token) setToken(session.provider_token);
    if (store.session.user) return;
    try { onSignedIn(await exchange(session), false); } catch { /* the card form still works */ }
  };
  const { data } = await sb.auth.getSession();
  await handle(data.session);
  sb.auth.onAuthStateChange((evt, session) => { if (evt === "SIGNED_IN") handle(session); });
}

export async function signInWithGitHub() {
  const sb = await supabase();
  await sb.auth.signInWithOAuth({ provider: "github", options: { redirectTo: `${location.origin}/app#feed`, scopes: "read:user repo" } });
}

/** E-mail + passphrase through Supabase. Returns {user} or {needsConfirm} ; throws on bad credentials. */
export async function emailAuth(email, password, { create = false } = {}) {
  const sb = await supabase();
  const { data, error } = create
    ? await sb.auth.signUp({ email, password, options: { emailRedirectTo: `${location.origin}/app#feed` } })
    : await sb.auth.signInWithPassword({ email, password });
  if (error) { const e = new Error(error.message); e.code = error.status; throw e; }
  if (!data.session) return { needsConfirm: true };
  return { user: await exchange(data.session) };
}

export async function supabaseSignOut() {
  if (!client) return;
  await client.auth.signOut().catch(() => {});
}
