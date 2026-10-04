// Punchy: the mascot is a living IBM punch card. It bends like paper, follows the cursor with two
// punched eyes and blinks. While a run is in the machine it punches real Hollerith holes across
// itself (the holes are cut out of the canvas, so the page shows through). A passing verdict flips it
// over to its Python side and it smiles; a failing one makes it shudder and frown.
//
// API (unchanged from the old keystone): createKeystone(canvas) -> {pulse(kind), busy(on), hide(on), destroy()}

import { hollerith } from "./punchcard.js";

const C = {
  cream: [247, 241, 226], lime: [229, 255, 195], green: [186, 242, 74], teal: [1, 51, 48],
  peri: [137, 176, 255], peach: [255, 166, 128], indigo: [25, 0, 102], rust: [140, 40, 10],
};
const W = 1, H = 0.44, CUT = 0.17;              // card half-width/height (real card ratio), corner cut
const NX = 26, NY = 10, COLS = 40, ROWS = 12;
const FACE = { x0: -0.5, x1: 0.5, y0: -0.34, y1: 0.3 };   // keep the face clear of data holes

const lerp = (a, b, t) => a + (b - a) * t;
const mix = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const rgb = (c, a = 1) => `rgba(${c.map((v) => Math.round(Math.max(0, Math.min(255, v)))).join(",")},${a})`;
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm = (a) => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const easeInOut = (t) => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** Holes for a word, as Set("col:row"), skipping the face area. */
function patternFor(word) {
  const out = new Set();
  [...word].forEach((ch, i) => {
    const col = 4 + i * 3;
    if (col >= COLS) return;
    hollerith(ch).forEach((row) => out.add(`${col}:${row}`));
  });
  return out;
}

export function createKeystone(canvas) {
  const ctx = canvas.getContext("2d");
  const LIGHT = norm([-.35, .6, 1]);
  const CAM = 4.4;
  const st = {
    t: 0, last: 0, mx: 0, my: -.2, yaw: 0, pitch: 0, roll: 0,
    flip: 0, flipFrom: 0, flipTo: 0, flipT: 1, flipDur: 1.1,
    flash: 0, flashCol: C.green, shake: 0, mood: 0, moodKind: "",     // mood: >0 happy/sad timer
    blink: 0, nextBlink: 2.5, busy: false, punch: 0, punched: new Map(),
    hidden: 0, hideT: 0, scale: 1, scaleV: 0, near: 0,
  };
  const ident = patternFor("LEGACYBRIDGE");
  let w = 0, hgt = 0, dpr = 1, raf = 0, cx = 0, cy = 0, R = 0;

  function resize() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    w = r.width; hgt = r.height;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(hgt * dpr);
  }

  function onMove(e) {
    const r = canvas.getBoundingClientRect();
    const ex = r.left + r.width / 2, ey = r.top + r.height * .5;
    st.mx = Math.max(-1, Math.min(1, (e.clientX - ex) / (window.innerWidth * .5)));
    st.my = Math.max(-1, Math.min(1, (e.clientY - ey) / (window.innerHeight * .7)));
    st.nearTarget = Math.hypot(e.clientX - ex, e.clientY - ey) < 260 ? 1 : 0;
  }

  // ---------------------------------------------------------------- geometry
  function cutCorner(x, y) {                        // the famous clipped top-left corner
    const k = (x + W) + (H - y);
    if (k < CUT) { const d = (CUT - k) / 2; x += d; y -= d; }
    return [x, y];
  }
  function deform(x, y) {
    const wave = st.busy ? .07 : .028;
    const z = .13 * (x * x - .35)                                          // the card is a little cupped
      + Math.sin(st.t * 2.1 + x * 2.4 + y * .9) * wave
      + Math.sin(st.t * 1.3 - y * 3) * .012;
    return [x, y, z];
  }
  function rotate([x, y, z]) {
    const yaw = st.yaw + st.flip, pitch = st.pitch, roll = st.roll;
    let c = Math.cos(yaw), s = Math.sin(yaw);
    [x, z] = [x * c + z * s, -x * s + z * c];
    c = Math.cos(pitch); s = Math.sin(pitch);
    [y, z] = [y * c - z * s, y * s + z * c];
    c = Math.cos(roll); s = Math.sin(roll);
    [x, y] = [x * c - y * s, x * s + y * c];
    return [x, y, z];
  }
  const world = (x, y) => rotate(deform(...cutCorner(x, y)));
  const project = ([x, y, z]) => { const k = CAM / (CAM - z); return [cx + x * R * k, cy - y * R * k]; };
  const P = (x, y) => project(world(x, y));

  function poly(pts) {
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
  }
  function rectOn(x0, y0, x1, y1) { poly([P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)]); }
  function ellipseOn(ex, ey, rx, ry, n = 18) {
    const pts = [];
    for (let i = 0; i < n; i++) { const a = (i / n) * Math.PI * 2; pts.push(P(ex + Math.cos(a) * rx, ey + Math.sin(a) * ry)); }
    poly(pts);
  }
  function lineOn(points) {
    ctx.beginPath();
    points.forEach(([x, y], i) => { const [sx, sy] = P(x, y); i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy); });
  }

  // ---------------------------------------------------------------- frame loop
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(.05, (now - (st.last || now)) / 1000);
    st.last = now; st.t += dt;
    const ease = (k) => 1 - Math.pow(k, dt);
    st.yaw = lerp(st.yaw, st.mx * .5, ease(.004));
    st.pitch = lerp(st.pitch, -.18 + st.my * .28, ease(.004));
    st.roll = lerp(st.roll, -st.mx * .07 + Math.sin(st.t * .8) * .03, ease(.01));
    st.near = lerp(st.near, st.nearTarget || 0, ease(.02));
    if (st.flipT < 1) {
      st.flipT = Math.min(1, st.flipT + dt / st.flipDur);
      st.flip = lerp(st.flipFrom, st.flipTo, easeInOut(st.flipT));
    }
    st.scaleV += (1 - st.scale) * 170 * dt; st.scaleV *= Math.pow(.001, dt); st.scale += st.scaleV * dt;
    st.flash = Math.max(0, st.flash - dt * 1.2);
    st.shake = Math.max(0, st.shake - dt * 1.8);
    st.mood = Math.max(0, st.mood - dt);
    st.hideT = lerp(st.hideT, st.hidden, ease(.004));
    // blink every few seconds (twice, sometimes)
    st.nextBlink -= dt;
    if (st.nextBlink <= 0) { st.blink = 1; st.nextBlink = 2.2 + Math.random() * 3.5; if (Math.random() < .25) st.nextBlink = .28; }
    st.blink = Math.max(0, st.blink - dt * 6.5);
    // the punch head walks across the card while busy
    if (st.busy) {
      const before = Math.floor(st.punch);
      st.punch += dt * 9;
      for (let c = before; c < Math.floor(st.punch); c++) {
        const col = c % COLS;
        if (col === 0) st.punched.clear();
        const ch = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"[Math.floor(Math.random() * 38)];
        st.punched.set(col, hollerith(ch));
      }
    }
    draw();
  }

  // ---------------------------------------------------------------- drawing
  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hgt);
    if (st.hideT > .995) return;
    R = Math.min(w * .42, hgt * .82) * st.scale * (1 + st.near * .03);
    cx = w / 2 + Math.sin(st.t * 60) * 8 * st.shake;
    cy = hgt * .58 + Math.sin(st.t * 1.5) * 4 + st.hideT * hgt * 1.2;

    // 1. the sheet: shaded quads, painter-sorted; front = cream card, back = lime Python side
    const faces = [];
    for (let j = 0; j < NY; j++) {
      for (let i = 0; i < NX; i++) {
        const x0 = -W + (2 * W * i) / NX, x1 = -W + (2 * W * (i + 1)) / NX;
        const y0 = -H + (2 * H * j) / NY, y1 = -H + (2 * H * (j + 1)) / NY;
        const q = [world(x0, y0), world(x1, y0), world(x1, y1), world(x0, y1)];
        const n = norm(cross(sub(q[1], q[0]), sub(q[3], q[0])));
        const cen = [(q[0][0] + q[2][0]) / 2, (q[0][1] + q[2][1]) / 2, (q[0][2] + q[2][2]) / 2];
        const front = dot(n, sub([0, 0, CAM], cen)) > 0;
        faces.push({ q, n: front ? n : n.map((v) => -v), front, z: cen[2] });
      }
    }
    faces.sort((a, b) => a.z - b.z);
    const tint = st.moodKind === "bad" ? Math.min(1, st.mood) * .35 : 0;
    for (const f of faces) {
      const base = f.front ? mix(C.cream, C.peach, tint) : C.lime;
      const lit = .78 + .3 * Math.max(0, dot(f.n, LIGHT));
      let col = base.map((v) => v * lit);
      if (st.flash > 0) col = mix(col, st.flashCol, st.flash * .5);
      const fill = rgb(col);
      poly(f.q.map(project));
      ctx.fillStyle = fill; ctx.strokeStyle = fill; ctx.lineWidth = .8; ctx.lineJoin = "round";
      ctx.fill(); ctx.stroke();
    }
    // which side faces us overall (centre normal)
    const c0 = world(0, 0), cn = norm(cross(sub(world(.05, 0), c0), sub(world(0, .05), c0)));
    const frontVisible = dot(cn, sub([0, 0, CAM], c0)) > 0;
    // outline
    const outline = [];
    const steps = 24;
    for (let i = 0; i <= steps; i++) outline.push([-W + (2 * W * i) / steps, -H]);
    for (let i = 0; i <= 8; i++) outline.push([W, -H + (2 * H * i) / 8]);
    for (let i = steps; i >= 0; i--) outline.push([-W + (2 * W * i) / steps, H]);
    for (let i = 8; i >= 0; i--) outline.push([-W, -H + (2 * H * i) / 8]);
    poly(outline.map(([x, y]) => P(x, y)));
    ctx.strokeStyle = rgb(C.teal, .28); ctx.lineWidth = 1.2; ctx.stroke();

    if (frontVisible) drawFront(); else drawBack();
  }

  function holeRect(col, row) {
    const cw = (2 * W - .14) / COLS, rh = (2 * H - .12) / ROWS;
    const x = -W + .07 + col * cw, y = H - .07 - (row + 1) * rh;       // row 0 ("12") is at the top
    return [x + cw * .22, y + rh * .2, x + cw * .78, y + rh * .8];
  }
  const inFace = ([x0, y0, x1, y1]) => x1 > FACE.x0 && x0 < FACE.x1 && y1 > FACE.y0 && y0 < FACE.y1;

  function drawFront() {
    // printed row digits as faint ticks, then the punched holes (really cut out)
    ctx.fillStyle = rgb(C.teal, .13);
    for (let col = 0; col < COLS; col += 1) {
      for (let row = 2; row < ROWS; row++) {
        const r = holeRect(col, row);
        if (inFace(r)) continue;
        const mx = (r[0] + r[2]) / 2, my = (r[1] + r[3]) / 2;
        rectOn(mx - .004, my - .009, mx + .004, my + .009);
        ctx.fill();
      }
    }
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = "#000";
    const holes = [];
    if (st.busy || st.punched.size) st.punched.forEach((rows, col) => rows.forEach((row) => holes.push([col, row])));
    else ident.forEach((k) => { const [c, r] = k.split(":").map(Number); holes.push([c, r]); });
    for (const [col, row] of holes) {
      const r = holeRect(col, row);
      if (inFace(r)) continue;
      rectOn(...r); ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
    // the punch head while busy
    if (st.busy) {
      const col = Math.floor(st.punch) % COLS;
      const r = holeRect(col, 0);
      ctx.fillStyle = rgb(C.indigo, .55);
      rectOn(r[0] - .01, H - .04, r[2] + .01, H - .005); ctx.fill();
    }
    drawFace();
  }

  function drawFace() {
    const ink = rgb(C.teal);
    const happy = st.moodKind === "ok" && st.mood > 0, sad = st.moodKind === "bad" && st.mood > 0;
    const eyeY = .06, eyeX = .25, rx = .085 + st.near * .01;
    const open = Math.max(.08, 1 - Math.sin(Math.min(1, st.blink) * Math.PI));
    // pupils follow the cursor, or the punch head while busy
    let lx = st.mx, ly = -st.my;
    if (st.busy) { lx = ((Math.floor(st.punch) % COLS) / COLS) * 2 - 1; ly = .9; }
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    for (const side of [-1, 1]) {
      const ex = side * eyeX;
      if (happy) {                                 // ^ ^
        lineOn([[ex - rx, eyeY - .04], [ex, eyeY + .07], [ex + rx, eyeY - .04]]);
        ctx.strokeStyle = ink; ctx.lineWidth = R * .045; ctx.stroke();
        continue;
      }
      if (sad) {                                   // slanted, worried
        lineOn([[ex - rx, eyeY + side * .03], [ex + rx, eyeY - side * .03]]);
        ctx.strokeStyle = ink; ctx.lineWidth = R * .045; ctx.stroke();
        continue;
      }
      ellipseOn(ex, eyeY, rx, .11 * open);
      ctx.fillStyle = ink; ctx.fill();
      if (open > .3) {
        const px = ex + lx * rx * .42, py = eyeY + ly * .045 * open + .02;
        ellipseOn(px, py, rx * .32, .032 * open, 12);
        ctx.fillStyle = "#fff"; ctx.fill();
      }
    }
    // mouth: a row of punched holes along a curve
    const curve = happy ? .07 : sad ? -.05 : st.busy ? 0 : .025;
    const n = happy ? 7 : 5;
    ctx.fillStyle = ink;
    for (let i = 0; i < n; i++) {
      const u = (i / (n - 1)) * 2 - 1;
      const x = u * (happy ? .17 : .12), y = -.16 - curve * (1 - u * u) + (sad ? .02 : 0);
      rectOn(x - .014, y - .022, x + .014, y + .022); ctx.fill();
    }
    if (st.busy) { ellipseOn(0, -.16, .03, .035); ctx.fill(); }   // a little "o" of concentration
    // cheeks blush when happy or flustered
    if (happy || sad) {
      ctx.fillStyle = rgb(happy ? C.green : C.peach, .55);
      for (const side of [-1, 1]) { ellipseOn(side * .43, -.08, .07, .035); ctx.fill(); }
    }
  }

  function drawBack() {
    // the Python side: indented code bars and a face in negative
    const lines = [[0, .55], [1, .8], [1, .45], [2, .7], [1, .35], [0, .6], [1, .9]];
    const lh = (2 * H - .2) / lines.length;
    lines.forEach(([indent, len], i) => {
      const y = H - .12 - i * lh;
      const x0 = W - .1 - indent * .12;               // drawn mirrored: we see this side from behind
      ctx.fillStyle = rgb(C.teal, .85);
      rectOn(x0 - len * .8, y - lh * .28, x0 - .02 - len * .25, y + lh * .28); ctx.fill();
      ctx.fillStyle = rgb(C.peri, .95);
      rectOn(x0 - .02 - len * .22, y - lh * .28, x0, y + lh * .28); ctx.fill();
    });
  }

  // ---------------------------------------------------------------- api
  function flipOnce(dur = 1.1) {
    const base = Math.round(st.flip / (Math.PI * 2)) * Math.PI * 2;
    st.flipFrom = st.flip; st.flipTo = base + Math.PI * 2; st.flipT = 0; st.flipDur = dur;
  }
  const api = {
    pulse(kind = "ok") {
      if (kind === "ok") { st.flash = .9; st.flashCol = C.green; st.mood = 2.2; st.moodKind = "ok"; st.scaleV += 3; flipOnce(1.2); }
      else if (kind === "bad") { st.flash = .6; st.flashCol = C.peach; st.shake = 1; st.mood = 1.8; st.moodKind = "bad"; }
      else if (kind === "card") { st.flash = .8; st.flashCol = C.lime; st.mood = 1.6; st.moodKind = "ok"; st.scaleV += 4; flipOnce(.9); }
    },
    busy(on) {
      on = !!on;
      if (on && !st.busy) { st.punch = 0; st.punched.clear(); }
      if (!on) st.punched.clear();
      st.busy = on;
    },
    hide(on) { st.hidden = on ? 1 : 0; },
    destroy() { cancelAnimationFrame(raf); window.removeEventListener("pointermove", onMove); window.removeEventListener("resize", resize); },
  };

  resize();
  window.addEventListener("resize", resize);
  window.addEventListener("pointermove", onMove, { passive: true });
  raf = requestAnimationFrame(frame);
  return api;
}
