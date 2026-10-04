// The story page: loader, the hero (a teal panel folds into a laptop, the COBOL becomes Python,
// then the code's own letters fly out and become "Diff everything"), the marquee, the wiring
// diagram, the sandbox vault and the numbers. Every nav click goes through the page transition.

import { $, $$, api, emit, reducedMotion, store } from "./api.js";
import { paint } from "./highlight.js";
import { drawLevel, invalidate, prime, resolve, scrub } from "./pixel.js";
import { initShell, loadSession, markNav, renderChip } from "./shell.js";
import { arrive, arriving, isBusy, reset } from "./transition.js";

const gsap = window.gsap;
const ST = window.ScrollTrigger;
const MOTION = !!(gsap && ST) && !reducedMotion();
const WIDE = () => window.matchMedia("(min-width: 961px)").matches;
const root = document.documentElement;
root.classList.add("js");
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

let lenis = null, keystone = null, heroTrigger = null;
const arrival = arriving();

const COBOL = `       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTCALC.
      * Interest accrual between two dates. Three methods: simple actual/365, 30/360 and daily compounding, all ROUNDED half-up like the bank's ledger expects.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY DATEPARM.
       01  WS-DAYS360          PIC S9(7)        COMP-3.
       01  WS-CBAL             PIC S9(11)V9(6)  COMP-3.
       LINKAGE SECTION.
       COPY INTPARM.
       PROCEDURE DIVISION USING INT-PARMS.
       0000-MAIN.
           MOVE 'V' TO DP-FUNCTION
           CALL 'DATEUTIL' USING DATE-PARMS
           EVALUATE IP-METHOD
             WHEN 'S'
               PERFORM 1000-ACTUAL-DAYS
               COMPUTE IP-INTEREST ROUNDED = IP-PRINCIPAL * IP-ANNUAL-RATE * IP-DAYS / 365
                 ON SIZE ERROR
                   MOVE 40 TO IP-RETURN-CODE
               END-COMPUTE
             WHEN 'M'
               PERFORM 2000-DAYS-360
               COMPUTE IP-INTEREST = IP-PRINCIPAL * IP-ANNUAL-RATE * WS-DAYS360 / 360
             WHEN 'C'
               PERFORM 3000-COMPOUND-DAILY
             WHEN OTHER
               MOVE 30 TO IP-RETURN-CODE
           END-EVALUATE
           GOBACK.`;

const PYTHON = `# module: INTCALC  (verified: 54/54 golden tests identical)
from decimal import Decimal
import dateutil, lbrt

def intcalc(p: dict) -> None:
    """Interest accrual between two dates; mirrors INTCALC.cbl, including its truncation rules."""
    p["IP-RETURN-CODE"] = Decimal(0)
    date_parms["DP-FUNCTION"] = "V"
    dateutil.dateutil(date_parms)
    method = p["IP-METHOD"]
    if method == "S":
        _actual_days(p)
        v = p["IP-PRINCIPAL"] * p["IP-ANNUAL-RATE"] * p["IP-DAYS"] / 365
        if lbrt.overflows(v, 7, 2):
            p["IP-RETURN-CODE"] = 40
        else:
            p["IP-INTEREST"] = lbrt.store(v, 7, 2, rounded=True)
    elif method == "M":
        _days_360(p)   # truncates, exactly like the COBOL
        p["IP-INTEREST"] = lbrt.store(p["IP-PRINCIPAL"] * p["IP-ANNUAL-RATE"] * ws["WS-DAYS360"] / 360, 7, 2)
    elif method == "C":
        _compound_daily(p)
    else:
        p["IP-RETURN-CODE"] = 30`;

// ------------------------------------------------------------------ smooth scroll + jumps
function initScroll() {
  if (!MOTION) return;
  gsap.registerPlugin(ST);
  if (window.Lenis) {
    lenis = new window.Lenis({ lerp: .09, smoothWheel: true });
    lenis.on("scroll", ST.update);
    gsap.ticker.add((t) => lenis.raf(t * 1000));
    gsap.ticker.lagSmoothing(0);
  }
}

/** Under the shutter: put the section at the top of the viewport, instantly. */
function jump(hash) {
  let y = 0;
  if (hash !== "#top") {
    const el = $(hash);
    if (!el) return;
    y = el.getBoundingClientRect().top + window.scrollY;
    if (hash === "#numbers" || hash === "#wiring") y -= 10;
  }
  if (lenis) lenis.scrollTo(y, { immediate: true, force: true });
  else window.scrollTo(0, y);
  ST?.update();
  document.body.classList.remove("nav-away");
  markNav(hash === "#top" ? null : hash);
  history.replaceState(null, "", hash === "#top" ? "/" : hash);
}

// ------------------------------------------------------------------ loader / arrival
// rAF (and so gsap) is paused in hidden tabs: never let an animation promise hold the page hostage
const settle = (p, ms) => Promise.race([Promise.resolve(p).catch(() => {}), new Promise((r) => setTimeout(r, ms))]);

async function intro() {
  const loader = $("#loader");
  const fontsReady = Promise.race([document.fonts?.ready ?? Promise.resolve(), new Promise((r) => setTimeout(r, 1600))]);
  if (!MOTION || document.hidden) {
    await fontsReady;
    loader.remove();
    document.body.classList.remove("is-loading");
    if (arrival) await settle(arrive(arrival), 1500);
    return;
  }
  $$("[data-pixel]").forEach((el) => prime(el));
  if (arrival) {
    // came from the machine: the shutter is already down, skip the loader
    loader.remove();
    await fontsReady;
    document.body.classList.remove("is-loading");
    const target = location.hash && location.hash !== "#top" ? location.hash : null;
    if (target) jump(target);
    await settle(arrive(arrival), 1800);
    if (!target) await settle(resolve($(".hero-title"), { step: 60 }), 1500);
    return;
  }
  gsap.to(".loader-tape i", { width: "100%", duration: 1.1, ease: "power2.inOut" });
  await fontsReady;
  await settle(resolve($(".logo-word", loader), { step: 55 }), 1400);
  const wipe = gsap.to(loader, { yPercent: -100, duration: .95, ease: "expo.inOut", delay: .1 });
  const title = new Promise((r) => setTimeout(() => resolve($(".hero-title"), { step: 75 }).then(r), 450));
  gsap.from(".hero-sub, .hero-actions", { y: 40, opacity: 0, duration: 1.1, ease: "expo.out", stagger: .1, delay: .75, clearProps: "opacity,transform" });
  gsap.from(".nav > *:not(.rail)", { y: -30, opacity: 0, duration: 1, ease: "expo.out", stagger: .06, delay: .55, clearProps: "opacity,transform" });
  gsap.from("#keystone", { yPercent: 110, duration: 1.4, ease: "expo.out", delay: .7, clearProps: "transform" });
  await settle(wipe, 1400);
  loader.remove();
  document.body.classList.remove("is-loading");
  lenis?.start();
  await settle(title, 1500);
}

// ------------------------------------------------------------------ section colours + nav
const setNav = (t) => { document.body.dataset.nav = t; };
const dock = (on) => $("#keystone").classList.toggle("is-docked", on);

function initSections() {
  const sections = $$("main > section, .footer");
  const enter = (sec) => {
    setNav(sec.dataset.theme === "light" ? "light" : "dark");
    markNav(sec.id && sec.id !== "hero" && sec.id !== "cta" ? "#" + sec.id : null);
    document.body.classList.toggle("rail-hidden", sec.id === "hero" && window.scrollY < innerHeight * .6);
    if (sec.id !== "hero") { dock(true); keystone?.hide(sec.classList.contains("footer")); }
  };
  if (!MOTION) {
    sections.forEach((s) => { if (s.id !== "hero") s.style.background = s.dataset.bg; });
    const obs = new IntersectionObserver((ents) => ents.forEach((e) => {
      if (!e.isIntersecting) return;
      enter(e.target);
      if (e.target.id === "hero") { setNav(window.scrollY < innerHeight * .6 ? "light" : "dark"); dock(false); }
    }), { rootMargin: "-45% 0px -50% 0px" });
    sections.forEach((s) => obs.observe(s));
    return;
  }
  sections.forEach((sec) => {
    if (sec.id === "hero") return;
    ST.create({ trigger: sec, start: "top 55%", end: "bottom 55%", onToggle: (self) => {
      if (!self.isActive) return;
      gsap.to(document.body, { backgroundColor: sec.dataset.bg, duration: .7, ease: "power2.out", overwrite: true });
      enter(sec);
    } });
  });
  ST.create({ trigger: "#hero", start: "top top", end: "bottom 55%", onToggle: (self) => {
    if (!self.isActive) return;
    gsap.to(document.body, { backgroundColor: "#E5FFC3", duration: .5, overwrite: true });
    markNav(null);
    dock(false);
    keystone?.hide(false);
  } });
}

function initNavAway() {
  let last = window.scrollY;
  window.addEventListener("scroll", () => {
    const y = window.scrollY, d = y - last;
    if (Math.abs(d) < 6) return;
    const past = !heroTrigger || y > heroTrigger.end;
    const keep = !$("#idPop").hidden || !$("#palette").hidden;
    document.body.classList.toggle("nav-away", past && d > 0 && !keep);
    last = y;
  }, { passive: true });
}

// ------------------------------------------------------------------ hero: panel -> laptop -> code -> heading
function offsetWithin(el, ancestor) {
  let x = 0, y = 0, n = el;
  while (n && n !== ancestor) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent; }
  return { x, y };
}

// ------------------------------------------------------------------ the pixel wipe: COBOL -> blocks -> Python
// The same glyph as the headings: the screen breaks into chunky blocks along a diagonal sweep and
// resolves into the other program. p in [0,2]: 0..1 blocks fill in, 1..2 they clear.
const WIPE_COLORS = ["#BAF24A", "#89B0FF", "#EAC2FF", "#FFA680", "#E5FFC3", "#CEE9FD", "#F4ECD2"];

function makeWipe(canvas, box) {
  const ctx = canvas.getContext("2d");
  let cells = [], W = 0, H = 0, B = 16, dpr = 1, last = -1;
  const size = () => {
    W = box.clientWidth; H = box.clientHeight;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.ceil(W * dpr); canvas.height = Math.ceil(H * dpr);
    B = Math.max(14, Math.round(W / 46));
    const cols = Math.ceil(W / B), rows = Math.ceil(H / B);
    cells = [];
    let max = 0;
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const n = Math.sin((c * 12.9898 + r * 78.233) * 43.7585) * 43758.5453;
      const rnd = n - Math.floor(n);
      const d = ((cols - 1 - c) / cols) * .62 + (r / rows) * .38 + rnd * .22;
      max = Math.max(max, d);
      cells.push({ x: c * B, y: r * B, d, color: WIPE_COLORS[(rnd * WIPE_COLORS.length) | 0] });
    }
    cells.forEach((k) => { k.d /= max; });
    last = -1;
  };
  const draw = (p) => {
    if (Math.abs(p - last) < .002) return;
    last = p;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (p <= 0 || p >= 2) return;
    const q = p < 1 ? p * 1.25 : (p - 1) * 1.25;
    for (const k of cells) {
      const s = p < 1 ? Math.min(1, Math.max(0, (q - k.d) * 7)) : Math.min(1, Math.max(0, (k.d - q) * 7) + (q < .02 ? 1 : 0));
      if (s <= 0) continue;
      const g = B * s - 2;
      if (g <= 0) continue;
      ctx.fillStyle = k.color;
      ctx.fillRect(k.x + (B - g) / 2, k.y + (B - g) / 2, g, g);
    }
  };
  size();
  return { size, draw };
}

function initHero() {
  const stage = $("#heroStage");
  paint($("#heroCobol"), COBOL, "cobol", { hl: new Set([18, 19, 20]) });
  paint($("#heroPython"), PYTHON, "python", { hl: new Set([13, 14, 15, 16, 17]) });
  const chapter = $("#heroChapter");
  if (!MOTION || !WIDE()) { root.classList.add("is-static"); return; }

  const laptop = $("#laptop"), lid = $("#lid"), screen = $("#screen"), body = $("#winBody");
  const cobolPre = $("#heroCobol"), cobolPane = $("#heroCobolPane"), pyPane = $("#heroPyPane");
  const tag = $("#winTag"), status = $("#winStatus"), ink = $("#winInk"), tabs = $$(".win-tab");
  const stamp = $("#screenStamp"), cols = $$("#heroPanel i");
  const wipe = makeWipe($("#pixWipe"), body);
  const wp = { p: 0 };

  const moveInk = (tab) => { ink.style.width = tab.offsetWidth + "px"; ink.style.transform = `translateX(${tab.offsetLeft}px)`; };
  moveInk(tabs[0]);
  let shownPy = false;
  const syncWipe = () => {
    wipe.draw(wp.p);
    const py = wp.p >= 1;
    if (py === shownPy) return;
    shownPy = py;
    pyPane.style.visibility = py ? "visible" : "hidden";
    cobolPane.style.visibility = py ? "hidden" : "visible";
    tabs.forEach((t, j) => t.classList.toggle("is-on", (j === 1) === py));
    moveInk(tabs[py ? 1 : 0]);
    tag.textContent = py ? "Python 3" : "GnuCOBOL";
  };

  // zoom: scale the laptop so its screen fills the stage, centred
  const zoom = () => {
    const sw = stage.clientWidth, sh = stage.clientHeight;
    const o = offsetWithin(screen, laptop);
    const dx = o.x + screen.offsetWidth / 2 - laptop.offsetWidth / 2;
    const dy = o.y + screen.offsetHeight / 2 - laptop.offsetHeight / 2;
    const s = Math.max(sw / screen.offsetWidth, sh / screen.offsetHeight) * 1.12;
    return { s, x: -s * dx, y: (sh / 2 - laptop.offsetTop) - s * dy };
  };
  let Z = zoom();

  const T_HEAD = 6.95, HEAD_DUR = .9;
  let headLevel = null;
  const syncHead = (t) => {
    const hp = (t - T_HEAD) / HEAD_DUR;
    const key = hp <= 0 ? -1 : hp >= 1 ? 99 : Math.floor(hp * 8);
    if (key === headLevel) return;
    headLevel = key;
    if (hp <= 0) drawLevel(chapter, -1);
    else scrub(chapter, Math.min(1, hp));
  };
  chapter.style.visibility = "visible";
  drawLevel(chapter, -1);

  gsap.set(laptop, { xPercent: -50, yPercent: -50 });
  gsap.set(lid, { scaleY: .03, transformOrigin: "50% 100%" });
  gsap.set(laptop, { y: () => innerHeight * .62, scale: .86 });
  gsap.set(".win-bar, .win-status", { opacity: 0 });
  gsap.set($$(".cl", cobolPre), { clipPath: "inset(0 100% 0 0)" });

  const tl = gsap.timeline({
    defaults: { ease: "none" },
    scrollTrigger: {
      trigger: stage, start: "top top", end: "+=5600", pin: true, scrub: .7, anticipatePin: 1, invalidateOnRefresh: true, refreshPriority: 1,
      onRefresh: () => { wipe.size(); headLevel = null; invalidate(chapter); syncWipe(); syncHead(tl.time()); },
    },
  });
  heroTrigger = tl.scrollTrigger;
  ST.addEventListener("refreshInit", () => { Z = zoom(); });

  tl.to(".hero-copy", { y: -140, opacity: 0, duration: .8, ease: "power2.in" }, 0)
    // the opening panel lifts away as a shutter of punch cards
    .to(cols, { yPercent: -106, duration: .9, ease: "power2.inOut", stagger: { each: .06, from: "center" } }, .15)
    // the laptop rises, closed, and its lid pops up from the base
    .to(laptop, { y: 0, scale: 1, duration: 1.1, ease: "power3.out" }, .45)
    .to(lid, { scaleY: 1, duration: .9, ease: "back.out(1.3)" }, 1.25)
    .fromTo(screen, { backgroundColor: "#06100f" }, { backgroundColor: "#013330", duration: .35 }, 2.0)
    .to(".win-bar, .win-status", { opacity: 1, duration: .3 }, 2.1)
    // the COBOL is typed in line by line, then scrolls
    .to($$(".cl", cobolPre), { clipPath: "inset(0 0% 0 0)", duration: .25, stagger: .028, ease: "power1.out" }, 2.3)
    .to(cobolPre, { y: () => -Math.max(0, cobolPre.scrollHeight - body.offsetHeight * .82), duration: .7, ease: "power1.inOut" }, 3.4)
    // pixel wipe: blocks sweep in, the program underneath becomes Python, blocks sweep out
    .to(wp, { p: 2, duration: 1.3, ease: "power1.inOut" }, 4.15)
    .call(() => {}, null, 5.45)
    // the verdict is stamped on the screen
    .fromTo(stamp, { opacity: 0, scale: 2.4, rotate: -16 }, { opacity: 1, scale: 1, rotate: -4, duration: .3, ease: "back.out(2)" }, 5.55)
    .to(status, { color: "#BAF24A", duration: .2 }, 5.6)
    // camera pushes into the screen; the code clears; the heading resolves out of pixels
    .to(laptop, { x: () => Z.x, y: () => Z.y, scale: () => Z.s, duration: .95, ease: "power2.inOut" }, 6.15)
    .to([".win-bar", ".win-status", stamp, pyPane], { opacity: 0, duration: .4 }, 6.15)
    .to({}, { duration: HEAD_DUR }, T_HEAD)
    .fromTo($$(".hero-bentos .bento"), { opacity: 0, y: 280, rotate: (i) => (i % 2 ? 9 : -9) },
      { opacity: 1, y: 0, rotate: 0, duration: .8, ease: "power3.out", stagger: .14 }, 7.7)
    .to({}, { duration: .9 });
  status.textContent = "cobc -x · compiled";
  // the scrubbed timeline lags the scroll, so frame-accurate state is synced from the timeline itself
  tl.eventCallback("onUpdate", () => {
    const t = tl.time();
    syncWipe();
    syncHead(t);
    const txt = t > 5.6 ? "54 / 54 identical" : wp.p >= 1 ? "verifying against GnuCOBOL…" : "cobc -x · compiled";
    if (status.textContent !== txt) status.textContent = txt;
    if (!heroTrigger?.isActive) return;
    setNav(t < .45 || t > 6.5 ? "light" : "dark");
    keystone?.hide(t > .25);
    document.body.classList.toggle("rail-hidden", t < .7);
  });
  $$(".hero-actions a").forEach((a) => a.addEventListener("focus", () => window.scrollTo(0, 0)));
}

// ------------------------------------------------------------------ marquee (ticker-driven, never jumps)
function initMarquee() {
  const rows = $$(".mq-row");
  rows.forEach((row) => {
    const track = $(".mq-track", row);
    track.append(...[...track.children].map((n) => n.cloneNode(true)));   // two identical halves
    row._x = 0; row._half = 0; row._speed = parseFloat(row.dataset.speed) || 1;
  });
  const measure = () => rows.forEach((row) => { row._half = $(".mq-track", row).scrollWidth / 2; });
  measure();
  document.fonts?.ready.then(measure);
  window.addEventListener("resize", measure);
  if (!MOTION) return;
  let boost = 0, dir = 1;
  ST.create({ trigger: ".how", start: "top bottom", end: "bottom top", onUpdate: (self) => {
    const v = self.getVelocity();
    boost = Math.min(6, Math.abs(v) / 400);
    if (Math.abs(v) > 30) dir = v > 0 ? 1 : -1;
  } });
  let skew = 0;
  gsap.ticker.add((_, dt) => {
    boost *= .94;
    const s = (1 + boost) * dir;
    skew += (boost * dir * 1.4 - skew) * .1;
    rows.forEach((row) => {
      if (!row._half) return;
      row._x -= row._speed * s * dt * .06;
      row._x = ((row._x % row._half) + row._half) % row._half;     // wrap into [0, half): seamless
      $(".mq-track", row).style.transform = `translate3d(${-row._x}px,0,0) skewX(${-skew * Math.sign(row._speed)}deg)`;
    });
  });
}

// ------------------------------------------------------------------ wiring: diagram drawn by scroll
const FLOW = ["deck", "analyze", "graph", "sched", "oracle", "translate", "verify", "repair", "fuzz", "integrate", "ship"];
const LOOPS = [["repair", "translate", "retry"], ["fuzz", "repair", "counterexample"]];

function initWiring() {
  const arch = $("#arch"), svg = $("#archWires"), grid = $("#archGrid"), token = $("#archToken");
  const node = (k) => $(`.arch-node[data-k="${k}"]`, grid);
  const NS = "http://www.w3.org/2000/svg";
  let mainPath = null, length = 0, segs = [];
  const center = (el) => {
    const a = arch.getBoundingClientRect(), r = el.getBoundingClientRect();
    return { x: r.left - a.left + r.width / 2, y: r.top - a.top + r.height / 2, w: r.width, h: r.height, l: r.left - a.left, t: r.top - a.top };
  };
  function draw() {
    svg.replaceChildren();
    const a = arch.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${a.width} ${a.height}`);
    // main flow: smooth curve through node centres, in order
    const pts = FLOW.map((k) => center(node(k)));
    let d = `M${pts[0].x},${pts[0].y}`;
    segs = [0];
    for (let i = 1; i < pts.length; i++) {
      const p = pts[i - 1], q = pts[i];
      if (Math.abs(p.y - q.y) < 10) d += ` L${q.x},${q.y}`;
      else { const my = (p.y + q.y) / 2; d += ` C${p.x},${my} ${q.x},${my} ${q.x},${q.y}`; }
    }
    const under = document.createElementNS(NS, "path");
    under.setAttribute("d", d); under.setAttribute("class", "wire-under");
    mainPath = document.createElementNS(NS, "path");
    mainPath.setAttribute("d", d); mainPath.setAttribute("class", "wire");
    svg.append(under, mainPath);
    length = mainPath.getTotalLength();
    mainPath.style.strokeDasharray = `${length}`;
    mainPath.style.strokeDashoffset = `${length * (1 - prog)}`;
    // where each node sits along the wire (for the reveal + token)
    segs = pts.map((p) => {
      let best = 0, bd = Infinity;
      for (let s = 0; s <= 200; s++) {
        const q = mainPath.getPointAtLength((s / 200) * length);
        const dd = Math.hypot(q.x - p.x, q.y - p.y);
        if (dd < bd) { bd = dd; best = s / 200; }
      }
      return best;
    });
    // feedback loops: dashed arcs outside the flow
    for (const [from, to, label] of LOOPS) {
      const p = center(node(from)), q = center(node(to));
      const loop = document.createElementNS(NS, "path");
      const sameRow = Math.abs(p.y - q.y) < 10;
      const lift = 44, lx = Math.min(p.l, q.l) - 22;
      const ld = sameRow
        ? `M${p.x},${p.t} C${p.x},${p.t - lift} ${q.x},${q.t - lift} ${q.x},${q.t}`
        : `M${p.l},${p.y} C${lx},${p.y} ${lx},${q.y} ${q.l},${q.y}`;
      loop.setAttribute("d", ld); loop.setAttribute("class", "wire-loop"); loop.dataset.from = from;
      const tx = document.createElementNS(NS, "text");
      tx.setAttribute("class", "wire-label");
      tx.textContent = label;
      const [up, lo] = p.y < q.y ? [p, q] : [q, p];
      const mid = sameRow ? { x: (p.x + q.x) / 2, y: p.t - lift * .62, a: "middle" } : { x: Math.min(p.l, q.l) + 12, y: (up.t + up.h + lo.t) / 2 + 4, a: "start" };
      tx.setAttribute("x", mid.x); tx.setAttribute("y", mid.y); tx.setAttribute("text-anchor", mid.a);
      svg.append(loop, tx);
    }
    // blackboard taps: every agent writes to the board
    const board = center($(".arch-board", grid));
    ["oracle", "translate", "verify", "repair", "fuzz", "integrate"].forEach((k) => {
      const p = center(node(k));
      const tap = document.createElementNS(NS, "path");
      tap.setAttribute("d", `M${p.x},${p.t + p.h} L${p.x},${board.t}`);
      tap.setAttribute("class", "wire-tap"); tap.dataset.k = k;
      svg.append(tap);
    });
  }
  let prog = MOTION ? 0 : 1;
  const apply = () => {
    if (!mainPath) return;
    mainPath.style.strokeDashoffset = `${length * (1 - prog)}`;
    const nodes = FLOW.map(node);
    nodes.forEach((n, i) => n.classList.toggle("is-lit", prog >= segs[i] - .005));
    const pt = mainPath.getPointAtLength(Math.max(0, Math.min(1, prog)) * length);
    const ahead = mainPath.getPointAtLength(Math.min(length, prog * length + 4));
    token.style.transform = `translate(${pt.x}px, ${pt.y}px) translate(-50%, -50%) rotate(${Math.atan2(ahead.y - pt.y, ahead.x - pt.x)}rad)`;
    token.style.opacity = prog > .002 && prog < .998 ? 1 : 0;
    // the board fills in as agents write
    const rows = $("#boardRows");
    const want = Math.round(prog * 18);
    while (rows.children.length < want) {
      const r = document.createElement("i");
      const kinds = ["status", "oracle", "verify", "attempt", "fuzz_round", "lesson", "scenario"];
      r.textContent = kinds[rows.children.length % kinds.length];
      rows.append(r);
    }
    while (rows.children.length > want) rows.lastChild.remove();
  };
  draw();
  apply();
  window.addEventListener("resize", () => { draw(); apply(); });
  document.fonts?.ready.then(() => { draw(); apply(); });
  // hover: light a node's wires
  $$(".arch-node", grid).forEach((n) => {
    n.addEventListener("mouseenter", () => { svg.querySelectorAll(`[data-k="${n.dataset.k}"], [data-from="${n.dataset.k}"]`).forEach((p) => p.classList.add("is-hot")); });
    n.addEventListener("mouseleave", () => { svg.querySelectorAll(".is-hot").forEach((p) => p.classList.remove("is-hot")); });
  });
  if (!MOTION) { $$(".arch-node", grid).forEach((n) => n.classList.add("is-lit")); return; }
  ST.create({ trigger: arch, start: "top 78%", end: "bottom bottom",  onUpdate: (self) => { prog = self.progress; apply(); }, onRefresh: () => { draw(); apply(); } });
  gsap.from($$(".arch-node", grid), { y: 60, opacity: 0, rotate: (i) => (i % 2 ? 4 : -4), duration: 1, ease: "expo.out", stagger: .05, clearProps: "transform,opacity",
    scrollTrigger: { trigger: arch, start: "top 85%", once: true } });
}

// ------------------------------------------------------------------ sandbox: the vault
const PROBES = [
  { cmd: "connect(api.example.com:443)", ring: "r1", verdict: "EPERM · network denied", ang: -32 },
  { cmd: "while True: pass", ring: "r3", verdict: "TIMEOUT · killed at 10 s", ang: 200 },
  { cmd: "write(out.dat, 4 GB)", ring: "r2", verdict: "EFBIG · rlimit fsize", ang: 128 },
  { cmd: "os.environ['GROQ_API_KEY']", ring: "r3", verdict: "KeyError · env scrubbed", ang: 20 },
];

function initVault() {
  const vault = $("#vault"), term = $("#coreTerm"), probes = $("#probes");
  const lines = ["$ ./INTCALC < test_017.in", "IP-INTEREST   0000123.45", "IP-RETURN-CODE 00", "exit 0 · 18 ms"];
  term.textContent = lines.join("\n");
  if (!MOTION) return;
  term.textContent = "";
  let tl = null;
  const build = () => {
    tl = gsap.timeline({ repeat: -1, repeatDelay: .4, paused: true });
    // the program runs, output types in
    lines.forEach((l, i) => tl.call(() => { term.textContent = lines.slice(0, i + 1).join("\n"); }, null, i * .35));
    let t = 1.6;
    for (const pr of PROBES) {
      const dot = document.createElement("div");
      dot.className = "probe";
      dot.innerHTML = `<span class="probe-cmd mono"></span><b class="probe-verdict mono"></b>`;
      dot.querySelector(".probe-cmd").textContent = pr.cmd;
      dot.querySelector(".probe-verdict").textContent = pr.verdict;
      probes.append(dot);
      const ring = $(`.${pr.ring}`, vault);
      const dist = { r1: .5, r2: .4, r3: .3 }[pr.ring];
      const rad = (pr.ang * Math.PI) / 180;
      tl.set(dot, { xPercent: -50, yPercent: -50, left: "50%", top: "50%", x: 0, y: 0, opacity: 0, scale: .6 }, t)
        .to(dot, { opacity: 1, scale: 1, duration: .25 }, t)
        .to(dot, { x: () => Math.cos(rad) * vault.offsetWidth * dist, y: () => Math.sin(rad) * vault.offsetHeight * dist, duration: .9, ease: "power2.in" }, t + .1)
        .call(() => { ring.classList.remove("is-hit"); void ring.offsetWidth; ring.classList.add("is-hit"); dot.classList.add("is-denied"); }, null, t + 1)
        .to(dot, { x: () => Math.cos(rad) * vault.offsetWidth * (dist - .06), y: () => Math.sin(rad) * vault.offsetHeight * (dist - .06), duration: .35, ease: "back.out(3)" }, t + 1)
        .to(dot, { opacity: 0, duration: .4, onComplete: () => dot.classList.remove("is-denied") }, t + 2.3);
      t += 1.3;
    }
    tl.call(() => { term.textContent = ""; }, null, t + 1.2);
  };
  build();
  ST.create({ trigger: vault, start: "top 85%", end: "bottom 10%", onToggle: (self) => (self.isActive ? tl.play() : tl.pause()) });
  gsap.fromTo(".vault .ring", { scale: .6, opacity: 0 }, { scale: 1, opacity: 1, duration: 1.2, ease: "expo.out", stagger: .12, scrollTrigger: { trigger: vault, start: "top 80%", once: true } });
  gsap.fromTo(".vault .core", { y: 60, opacity: 0, rotateX: 40 }, { y: 0, opacity: 1, rotateX: 0, duration: 1.2, ease: "expo.out", delay: .3, scrollTrigger: { trigger: vault, start: "top 80%", once: true } });
}

// ------------------------------------------------------------------ the rest
function initChapters() {
  if (!MOTION) return;
  // headings resolve the first time they are on screen (robust against jumps under the shutter);
  // a still-coarse heading is re-primed when fonts or the layout change so it never shows stale blocks
  const pending = new Set($$("[data-pixel]").filter((el) => !el.classList.contains("hero-title")));
  const io = new IntersectionObserver((ents) => ents.forEach((e) => {
    if (!e.isIntersecting) return;
    io.unobserve(e.target);
    pending.delete(e.target);
    resolve(e.target, { step: 70 });
  }), { rootMargin: "0px 0px -10% 0px" });
  pending.forEach((el) => io.observe(el));
  const reprime = () => pending.forEach((el) => { invalidate(el); prime(el); });
  document.fonts?.ready.then(reprime);
  let rt = 0;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(reprime, 150); });
  $$(".reveal").forEach((el) => {
    gsap.to(el, { opacity: 1, y: 0, duration: 1.1, ease: "expo.out", scrollTrigger: { trigger: el, start: "top 88%", once: true } });
  });
  gsap.from(".num-grid .block", { y: 140, opacity: 0, duration: 1.2, ease: "expo.out", stagger: .1, clearProps: "all", scrollTrigger: { trigger: ".num-grid", start: "top 85%", once: true } });
  $$(".bar i").forEach((b) => b.style.setProperty("--grow", 0));
  ST.create({ trigger: ".bars", start: "top 90%", once: true, onEnter: () => $$(".bar i").forEach((b, i) => setTimeout(() => b.style.setProperty("--grow", 1), i * 160)) });
  $$(".count").forEach((el) => {
    const end = parseFloat(el.dataset.count);
    if (!end) return;
    const o = { v: 0 };
    ST.create({ trigger: el, start: "top 90%", once: true, onEnter: () => gsap.to(o, { v: end, duration: 1.4, ease: "power3.out", onUpdate: () => { el.textContent = Math.round(o.v); } }) });
  });
  ST.create({ trigger: ".stamp-row", start: "top 92%", once: true, onEnter: () => $$(".stamp-row .stamp").forEach((el, i) => { el.style.animationDelay = `${i * 140}ms`; el.classList.add("is-stamped"); }) });
  gsap.fromTo("#ctaCard", { scale: .9, borderRadius: 80 }, { scale: 1, borderRadius: 40, ease: "none", scrollTrigger: { trigger: "#ctaCard", start: "top bottom", end: "top 30%", scrub: .6 } });
  gsap.fromTo(".cta-pcard", { y: 160, rotate: -30 }, { y: -80, rotate: -10, ease: "none", scrollTrigger: { trigger: "#ctaCard", start: "top bottom", end: "bottom top", scrub: true } });
  gsap.fromTo(".cta-pcard.two", { y: 220, rotate: 26 }, { y: -140, rotate: 8, ease: "none", scrollTrigger: { trigger: "#ctaCard", start: "top bottom", end: "bottom top", scrub: true } });
  gsap.from(".footer .logo-word-xl", { yPercent: 40, opacity: 0, duration: 1.3, ease: "expo.out", scrollTrigger: { trigger: ".footer", start: "top 85%", once: true } });
}

function initMagnets() {
  if (!MOTION || !window.matchMedia("(hover: hover)").matches) return;
  $$(".magnetic").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      gsap.to(el, { x: (e.clientX - r.left - r.width / 2) * .22, y: (e.clientY - r.top - r.height / 2) * .3, duration: .5, ease: "power3.out" });
    });
    el.addEventListener("pointerleave", () => gsap.to(el, { x: 0, y: 0, duration: .8, ease: "elastic.out(1, .45)" }));
  });
  $$(".bento, .block").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      gsap.to(el, { rotateX: ((e.clientY - r.top) / r.height - .5) * -5, rotateY: ((e.clientX - r.left) / r.width - .5) * 6, transformPerspective: 900, duration: .6, ease: "power3.out" });
    });
    el.addEventListener("pointerleave", () => gsap.to(el, { rotateX: 0, rotateY: 0, duration: .9, ease: "power3.out" }));
  });
}

async function boot() {
  window.scrollTo(0, 0);
  initScroll();
  lenis?.stop();
  const shell = initShell({
    jump,
    lenis: () => lenis,
    commands: () => [],
  });
  keystone = shell.keystone;
  initSections();
  const session = loadSession().then((s) => {
    renderChip(s.user, { onSignOut: async () => { await api("/api/auth/signout", { method: "POST" }).catch(() => {}); store.session.user = null; renderChip(null); } });
    $("#footerEngine").textContent = s.live_providers?.length ? `free-tier providers: ${s.live_providers.join(", ")}` : "offline mock engine (no API keys on this server)";
  }).catch(() => {});
  initHero();
  initMarquee();
  initWiring();
  initVault();
  initChapters();
  initMagnets();
  initNavAway();
  if (MOTION) {
    ST.sort();
    ST.refresh();
    session.then(() => ST.refresh());
    let rw = innerWidth;
    window.addEventListener("resize", () => {
      if (Math.abs(innerWidth - rw) < 40) return;
      rw = innerWidth;
      $$("[data-pixel]").forEach(invalidate);
    });
  }
  await intro();
  if (MOTION) ST.refresh();
}

/** Whatever happened during boot, the page must end up scrollable and clickable. */
function unlock() {
  document.body.classList.remove("is-loading");
  $("#loader")?.remove();
  if (!isBusy()) reset();
  lenis?.start();
}
const watchdog = setTimeout(unlock, 5000);
boot().catch((e) => console.error(e)).finally(() => { clearTimeout(watchdog); unlock(); });
emit("boot");
