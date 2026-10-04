// Punch-card identity. The handle is punched in real IBM 029 Hollerith code; the passphrase only
// punches a fixed mask (one hole per character), so the card never reveals it.

import { $, api, emit, h, store } from "./api.js";
import { emailAuth, enabled as supabaseOn } from "./supa.js";

const ROWS = ["12", "11", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
const COLS = 40;

export function hollerith(ch) {
  const c = ch.toUpperCase();
  const r = (...names) => names.map((n) => ROWS.indexOf(String(n)));
  if (/[0-9]/.test(c)) return r(c);
  if (c >= "A" && c <= "I") return r(12, c.charCodeAt(0) - 64);
  if (c >= "J" && c <= "R") return r(11, c.charCodeAt(0) - 73);
  if (c >= "S" && c <= "Z") return r(0, c.charCodeAt(0) - 81);
  return { ".": r(12, 3, 8), "-": r(11), "_": r(0, 5, 8), "@": r(8, 4), " ": [] }[c] ?? r(0, 8, 2);
}

/** Build a punch grid inside `grid` (once); returns a function(holes:Set<"col:row">, pw:Set) */
export function makeGrid(grid, cols = COLS) {
  grid.style.setProperty("--cols", cols);
  const cells = [];
  for (let row = 0; row < 12; row++) {
    for (let col = 0; col < cols; col++) {
      const s = document.createElement("span");
      s.textContent = row >= 2 ? ROWS[row] : "";
      s.style.gridRow = row + 1; s.style.gridColumn = col + 1;
      grid.append(s);
      cells.push(s);
    }
  }
  return (holes, pw = new Set()) => {
    cells.forEach((s, i) => {
      const key = `${i % cols}:${Math.floor(i / cols)}`;
      const on = holes.has(key) || pw.has(key);
      if (on !== s.classList.contains("is-hole")) s.classList.toggle("is-hole", on);
      s.classList.toggle("pw", pw.has(key));
    });
  };
}

export function holesFor(text, offset = 0, cols = COLS) {
  const out = new Set();
  [...text].slice(0, cols - offset).forEach((ch, i) => hollerith(ch).forEach((row) => out.add(`${i + offset}:${row}`)));
  return out;
}

/** Wire the identity card form. onSignedIn(user) runs after the card has been fed. */
export function initIdCard({ onSignedIn }) {
  const form = $("#idCard"), handle = $("#idHandle"), pass = $("#idPass"), confirm = $("#idConfirm");
  const confirmWrap = $("#idConfirmWrap"), mode = $("#idMode"), err = $("#idError"), submit = $("#idSubmit");
  const print = $("#idPrint"), card = $("#idPunch");
  const paint = makeGrid($("#idGrid"));
  let isNew = null, checkTimer = 0, lastChecked = "";

  const render = () => {
    const hd = handle.value.trim();
    const holes = holesFor(hd);
    const pw = new Set();
    const start = Math.min(COLS - 1, hd.length + 2);
    for (let i = 0; i < Math.min(pass.value.length, COLS - start); i++) pw.add(`${start + i}:${i % 2}`);
    paint(holes, pw);
    print.textContent = "LEGACYBRIDGE · " + (hd ? hd.toUpperCase() : "IDENTITY CARD");
  };

  const setMode = (exists) => {
    isNew = exists === null ? null : !exists;
    confirmWrap.hidden = !isNew;
    pass.autocomplete = isNew ? "new-password" : "current-password";
    submit.firstChild.textContent = isNew ? "Punch new card " : "Feed card ";
    mode.innerHTML = exists === null ? "Punch in or punch a new card. Same card, both ways."
      : exists ? `Welcome back, <b>${escapeHtml(handle.value.trim().toLowerCase())}</b>. Your passphrase, please.`
        : `<b>${escapeHtml(handle.value.trim().toLowerCase())}</b> is a fresh card. Choose a passphrase (8+) and punch it twice.`;
  };

  const isEmail = () => handle.value.includes("@");

  const check = async () => {
    const hd = handle.value.trim().toLowerCase();
    if (hd === lastChecked) return;
    lastChecked = hd;
    if (hd.includes("@")) {
      isNew = null; confirmWrap.hidden = true; submit.firstChild.textContent = "Feed card ";
      mode.innerHTML = supabaseOn() ? "An e-mail card: signs in with your Supabase account (or makes one)."
        : "E-mail cards need Supabase on this server. Punch a plain handle instead.";
      return;
    }
    if (!/^[a-z0-9][a-z0-9_.-]{2,31}$/.test(hd)) { setMode(null); return; }
    try { setMode((await api(`/api/auth/handle/${encodeURIComponent(hd)}`)).exists); } catch { setMode(null); }
  };

  handle.addEventListener("input", () => { render(); err.textContent = ""; clearTimeout(checkTimer); checkTimer = setTimeout(check, 280); });
  handle.addEventListener("blur", check);
  pass.addEventListener("input", () => { render(); err.textContent = ""; });
  confirm.addEventListener("input", () => { err.textContent = ""; });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await check();
    const hd = handle.value.trim().toLowerCase(), pw = pass.value;
    if (!hd) return fail("Punch a handle first.");
    if (isEmail()) return emailCard(hd, pw);
    if (isNew && pw.length < 8) return fail("Passphrase: at least 8 characters.");
    if (isNew && pw !== confirm.value) return fail("The two passphrases don't match.");
    submit.disabled = true;
    try {
      const res = await api(isNew ? "/api/auth/signup" : "/api/auth/signin", { method: "POST", body: { handle: hd, passphrase: pw } });
      pass.value = confirm.value = "";
      await feedAnimation();
      onSignedIn(res.user, res.new);
    } catch (ex) {
      fail(ex.message);
    } finally {
      submit.disabled = false;
    }
  });

  // e-mail handle -> Supabase Auth: sign in; if that fails, offer to create the account (punch twice)
  async function emailCard(email, pw) {
    if (!supabaseOn()) return fail("E-mail cards need Supabase on this server.");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return fail("That e-mail doesn't look right.");
    const creating = !confirmWrap.hidden;
    if (creating && pw.length < 8) return fail("Passphrase: at least 8 characters.");
    if (creating && pw !== confirm.value) return fail("The two passphrases don't match.");
    submit.disabled = true;
    try {
      const res = await emailAuth(email, pw, { create: creating });
      if (res.needsConfirm) {
        mode.innerHTML = `Almost there: confirm the e-mail we sent to <b>${escapeHtml(email)}</b>, then feed the card again.`;
        confirmWrap.hidden = true; confirm.value = "";
        return;
      }
      pass.value = confirm.value = "";
      await feedAnimation();
      onSignedIn(res.user, creating);
    } catch (ex) {
      if (!creating && /invalid/i.test(ex.message)) {
        confirmWrap.hidden = false;
        submit.firstChild.textContent = "Punch new card ";
        mode.innerHTML = `No card for <b>${escapeHtml(email)}</b> with that passphrase. New here? Punch it again to make one.`;
        err.textContent = "";
      } else fail(ex.message);
    } finally {
      submit.disabled = false;
    }
  }

  function fail(msg) {
    err.textContent = msg;
    card.classList.remove("shake"); void card.offsetWidth; card.classList.add("shake");
    emit("keystone", "bad");
  }

  function feedAnimation() {
    return new Promise((done) => {
      const light = $(".reader-light"), label = $("#readerLabel");
      form.classList.add("is-feeding");
      label.textContent = "READING CARD…";
      emit("keystone", "card");
      if (window.gsap) window.gsap.fromTo(light, { width: "0%" }, { width: "100%", duration: 1.1, ease: "power2.inOut" });
      setTimeout(() => {
        label.textContent = "CARD ACCEPTED · HOPPER OPEN";
        form.classList.remove("is-feeding");
        if (window.gsap) window.gsap.to(light, { width: "0%", duration: .6, delay: .6, ease: "power2.inOut" });
        done();
      }, 1150);
    });
  }

  render();
  return { focus: () => handle.focus({ preventScroll: true }), reset: () => { handle.value = ""; pass.value = ""; confirm.value = ""; lastChecked = ""; setMode(null); render(); } };
}

/** A small decorative hole pattern for a ledger card, from the run id. */
export function ledgerHoles(id) {
  const holes = h("div", { class: "lc-holes", "aria-hidden": "true" });
  const bits = holesFor(id.slice(0, 20), 0, 20);
  for (let row = 0; row < 6; row++) {
    for (let col = 0; col < 20; col++) {
      holes.append(h("i", { class: bits.has(`${col}:${row + 2}`) || bits.has(`${col}:${row}`) ? "h" : "" }));
    }
  }
  return holes;
}

export const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export { store };
