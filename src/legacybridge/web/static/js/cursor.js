// The cursor: a dot that is exactly where the pointer is, and a little punch card that chases it
// on a spring. The card leans into the motion, stretches with speed, wraps itself around buttons,
// turns into a caret over fields and a crosshair over graph nodes. Clicking stamps a tiny punch
// card that gets its holes punched and is fed away. Touch devices and reduced motion keep the native cursor.

import { reducedMotion } from "./api.js";

export function initCursor() {
  if (reducedMotion() || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return null;
  document.documentElement.classList.add("has-cursor");

  const root = document.createElement("div");
  root.className = "cursor";
  root.setAttribute("aria-hidden", "true");
  root.innerHTML = `<div class="cur-card"><span class="cur-holes"></span><b class="cur-label"></b></div><div class="cur-dot"></div>`;
  const canvas = document.createElement("canvas");
  canvas.className = "cur-chads";
  document.body.append(canvas, root);
  const card = root.querySelector(".cur-card"), dot = root.querySelector(".cur-dot"), label = root.querySelector(".cur-label");
  const ctx = canvas.getContext("2d");
  let dpr = 1;
  const size = () => {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = innerWidth * dpr; canvas.height = innerHeight * dpr;
    canvas.style.width = innerWidth + "px"; canvas.style.height = innerHeight + "px";
  };
  size();
  window.addEventListener("resize", size);

  // spring state: position, size, radius
  const m = { x: innerWidth / 2, y: innerHeight / 2 };
  const p = { x: m.x, y: m.y, vx: 0, vy: 0, w: 26, h: 18, vw: 0, vh: 0, r: 4, vr: 0, rot: 0 };
  const target = { w: 26, h: 18, r: 4, x: null, y: null };
  let mode = "idle", hot = null, hotR = 12, visible = false, pressed = false, busy = false;
  const marks = [];

  function setMode(next, text = "") {
    if (next !== mode) { root.dataset.mode = next; mode = next; }
    if (label.textContent !== text) label.textContent = text;
  }

  function classify(t) {
    if (!t || !t.closest) return { mode: "idle" };
    if (t.closest(".tx.is-on")) return { mode: "idle" };
    const custom = t.closest("[data-cursor]");
    const field = t.closest("input:not([type=checkbox]):not([type=radio]):not([type=file]), textarea, [contenteditable=true]");
    if (field) return { mode: "text", el: field };
    const node = t.closest(".graph .node, .arch-node");
    if (node) return { mode: "target", el: node, text: node.dataset.cursor || "" };
    const drop = t.closest(".dropzone");
    if (drop) return { mode: "drop", el: drop, text: "drop" };
    const btn = t.closest("a[href], button, [role=button], label.switch, .seg-btn, summary");
    if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") {
      const r = btn.getBoundingClientRect();
      if (r.width < 260 && r.height < 120 && !btn.classList.contains("no-snap")) return { mode: "snap", el: btn, text: custom?.dataset.cursor || "" };
      return { mode: "link", el: btn, text: custom?.dataset.cursor || "open" };
    }
    if (t.closest("pre.code, .code-wrap")) return { mode: "code", text: "{ }" };
    if (custom) return { mode: "link", text: custom.dataset.cursor };
    return { mode: "idle" };
  }

  window.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    m.x = e.clientX; m.y = e.clientY;
    if (!visible) { visible = true; root.classList.add("is-on"); p.x = m.x; p.y = m.y; }
    retarget(classify(e.target));
  }, { passive: true });
  function retarget(c) {
    if ((c.el || null) !== hot) {
      hot = c.el || null;
      hotR = hot ? parseFloat(getComputedStyle(hot).borderTopLeftRadius) || 12 : 12;
    }
    setMode(c.mode, c.text || "");
  }
  document.addEventListener("pointerleave", () => { visible = false; root.classList.remove("is-on"); });
  window.addEventListener("blur", () => { visible = false; root.classList.remove("is-on"); });
  window.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse") return;
    pressed = true;
    root.classList.add("is-down");
    p.vw -= 10; p.vh -= 7;                       // the punch: the card squashes and springs back
    punch(e.clientX, e.clientY, mode === "snap" || mode === "link");
  });
  window.addEventListener("pointerup", () => { pressed = false; root.classList.remove("is-down"); });
  // scrolling under a still pointer changes what it hovers
  window.addEventListener("scroll", () => {
    if (!visible) return;
    retarget(classify(document.elementFromPoint(m.x, m.y)));
  }, { passive: true });

  // a click punches a tiny card: it stamps down, holes are punched in Hollerith rows, then it is
  // fed up and away; a cut-corner outline ripples out from it
  function punch(x, y, big) {
    const cols = big ? 7 : 5, holes = [];
    for (let c = 0; c < cols; c++) {
      const rows = new Set([(Math.random() * 4) | 0]);
      if (Math.random() < .45) rows.add((Math.random() * 4) | 0);
      rows.forEach((r) => holes.push({ c, r }));
    }
    holes.sort(() => Math.random() - .5);
    marks.push({ x, y, t: 0, w: big ? 46 : 34, h: big ? 28 : 22, cols, holes, rot: (Math.random() - .5) * 14 });
    if (marks.length > 24) marks.shift();
  }

  function cutRect(c, x, y, w, h, k) {
    c.beginPath();
    c.moveTo(x + k, y); c.lineTo(x + w, y); c.lineTo(x + w, y + h); c.lineTo(x, y + h); c.lineTo(x, y + k); c.closePath();
  }

  function drawMarks() {
    const light = document.body.dataset.nav === "light";
    const ring = light ? "#E5FFC3" : "#013330";
    for (let i = marks.length - 1; i >= 0; i--) {
      const mk = marks[i];
      mk.t += 1;
      const t = mk.t;
      if (t > 62) { marks.splice(i, 1); continue; }
      const ease = (u) => 1 - Math.pow(1 - Math.min(1, Math.max(0, u)), 3);
      // ripple
      const rp = ease(t / 26);
      if (t < 26) {
        const s = 1 + rp * 1.3, w = mk.w * s, h = mk.h * s;
        ctx.save();
        ctx.globalAlpha = 1 - rp;
        ctx.strokeStyle = ring; ctx.lineWidth = 1.5;
        ctx.translate(mk.x, mk.y); ctx.rotate(mk.rot * Math.PI / 180);
        cutRect(ctx, -w / 2, -h / 2, w, h, 6 * s);
        ctx.stroke();
        ctx.restore();
      }
      // the card
      const stamp = t < 9 ? 1.5 - .5 * ease(t / 9) : 1;
      const feed = ease((t - 34) / 28);
      ctx.save();
      ctx.globalAlpha = Math.min(1, t / 4) * (1 - feed);
      ctx.translate(mk.x, mk.y - feed * 26);
      ctx.rotate((mk.rot * (1 - feed)) * Math.PI / 180);
      ctx.scale(stamp, stamp * (1 - feed * .35));
      ctx.fillStyle = "#F4ECD2";
      ctx.shadowColor = "rgba(1,51,48,.28)"; ctx.shadowOffsetY = 3; ctx.shadowBlur = 0;
      cutRect(ctx, -mk.w / 2, -mk.h / 2, mk.w, mk.h, 6);
      ctx.fill();
      ctx.shadowColor = "transparent";
      const cw = (mk.w - 10) / mk.cols, ch = (mk.h - 8) / 4;
      ctx.fillStyle = "#013330";
      mk.holes.forEach((hl, j) => {
        const at = 5 + j * 2;
        if (t < at) return;
        const pop = t < at + 3 ? 1.8 - .8 * ((t - at) / 3) : 1;
        const sz = Math.min(cw, ch) * .62 * pop;
        const hx = -mk.w / 2 + 6 + hl.c * cw + cw / 2, hy = -mk.h / 2 + 4 + hl.r * ch + ch / 2;
        ctx.fillStyle = t < at + 3 ? "#BAF24A" : "#013330";
        ctx.fillRect(hx - sz / 2, hy - sz / 2, sz, sz);
      });
      ctx.restore();
    }
  }

  function frame() {
    // targets per mode
    let tx = m.x, ty = m.y;
    target.w = 26; target.h = 18; target.r = 4;
    if (mode === "snap" && hot && hot.isConnected) {
      const r = hot.getBoundingClientRect();
      tx = r.left + r.width / 2 + (m.x - (r.left + r.width / 2)) * .12;    // stays a little magnetic
      ty = r.top + r.height / 2 + (m.y - (r.top + r.height / 2)) * .12;
      target.w = r.width + 14; target.h = r.height + 14;
      target.r = Math.min(hotR, r.height / 2) + 7;
    } else if (mode === "link") { target.w = 74; target.h = 74; target.r = 37; }
    else if (mode === "text") { target.w = 3; target.h = 30; target.r = 2; }
    else if (mode === "target") { target.w = 46; target.h = 46; target.r = 23; }
    else if (mode === "drop") { target.w = 86; target.h = 58; target.r = 10; }
    else if (mode === "code") { target.w = 54; target.h = 34; target.r = 10; }
    // springs (semi-implicit Euler)
    const k = mode === "text" ? .5 : .16, d = .72;
    p.vx = (p.vx + (tx - p.x) * k) * d; p.vy = (p.vy + (ty - p.y) * k) * d;
    p.x += p.vx; p.y += p.vy;
    p.vw = (p.vw + (target.w - p.w) * .2) * .68; p.vh = (p.vh + (target.h - p.h) * .2) * .68; p.vr = (p.vr + (target.r - p.r) * .2) * .7;
    p.w += p.vw; p.h += p.vh; p.r += p.vr;
    const speed = Math.hypot(p.vx, p.vy);
    const free = mode === "idle" || mode === "code";
    const stretch = free ? Math.min(speed * .018, .5) : 0;
    const ang = Math.atan2(p.vy, p.vx);
    p.rot += ((free ? Math.max(-28, Math.min(28, p.vx * 1.6)) : 0) - p.rot) * .2;
    card.style.width = Math.max(2, p.w) + "px";
    card.style.height = Math.max(2, p.h) + "px";
    card.style.borderRadius = Math.max(1, p.r) + "px";
    card.style.transform = `translate3d(${p.x}px, ${p.y}px, 0) translate(-50%, -50%) rotate(${ang}rad) scale(${1 + stretch}, ${1 - stretch * .55}) rotate(${-ang}rad) rotate(${p.rot}deg)`;
    dot.style.transform = `translate3d(${m.x}px, ${m.y}px, 0) translate(-50%, -50%) scale(${pressed ? .6 : busy ? 1.4 : 1})`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    drawMarks();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    busy(b) { busy = !!b; root.classList.toggle("is-busy", busy); },
    burst(x = m.x, y = m.y) { punch(x, y, true); },
  };
}
