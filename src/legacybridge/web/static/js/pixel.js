// Pixel-resolve: a heading is first drawn as chunky blocks (legacy), then resolves into the real
// typeface (modern). The text stays real DOM text; a canvas covers it while it is coarse.

const LEVELS = [2.6, 3.6, 5, 7, 10, 15, 24];   // font-size / block-size, coarse -> fine

function lines(el) {
  return el.innerHTML.split(/<br\s*\/?>/i).map((l) => {
    const d = document.createElement("div");
    d.innerHTML = l;
    return d.textContent.replace(/\s+/g, " ").trim();
  });
}

function setup(el) {
  if (el._px) return el._px;
  const cs = getComputedStyle(el);
  const canvas = document.createElement("canvas");
  canvas.className = "pixel-canvas";
  canvas.setAttribute("aria-hidden", "true");
  el.append(canvas);
  el._px = { canvas, color: el.dataset.pixelColor || cs.color, level: -1 };
  return el._px;
}

/** Draw `el` at resolution level i (0 = coarsest, LEVELS.length = done: real text). */
export function drawLevel(el, i) {
  const px = setup(el);
  if (i === px.level && !px.dirty) return;
  px.level = i;
  px.dirty = false;
  const { canvas } = px;
  if (i >= LEVELS.length || i < 0 && i !== -1) {
    canvas.style.display = "none";
    el.classList.remove("is-pixelating");
    return;
  }
  el.classList.add("is-pixelating");
  canvas.style.display = "block";
  const cs = getComputedStyle(el);
  const fs = parseFloat(cs.fontSize);
  const lh = parseFloat(cs.lineHeight) || fs * .9;
  const w = el.clientWidth, hgt = el.clientHeight;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.ceil(w * dpr);
  canvas.height = Math.ceil(hgt * dpr);
  canvas.style.width = w + "px";
  canvas.style.height = hgt + "px";
  if (i === -1) { canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height); return; } // hidden state

  const block = Math.max(2, fs / LEVELS[i]);
  const sw = Math.max(1, Math.ceil(w / block)), sh = Math.max(1, Math.ceil(hgt / block));
  const small = document.createElement("canvas");
  small.width = sw; small.height = sh;
  const s = small.getContext("2d", { willReadFrequently: true });
  const k = 1 / block;
  s.font = `${cs.fontWeight} ${fs * k}px ${cs.fontFamily}`;
  s.textBaseline = "alphabetic";
  if ("letterSpacing" in s) s.letterSpacing = `${(parseFloat(cs.letterSpacing) || 0) * k}px`;
  s.fillStyle = "#000";
  const align = cs.textAlign === "center" ? "center" : cs.textAlign === "right" || cs.textAlign === "end" ? "right" : "left";
  s.textAlign = align;
  const upper = cs.textTransform === "uppercase";
  const padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
  const x = align === "center" ? (w / 2) * k : align === "right" ? (w - padR) * k : padL * k;
  const ls = lines(el);
  const m = s.measureText("HG");
  const asc = m.fontBoundingBoxAscent ?? m.actualBoundingBoxAscent;
  const desc = m.fontBoundingBoxDescent ?? m.actualBoundingBoxDescent;
  const top0 = (parseFloat(cs.paddingTop) || 0) * k;
  ls.forEach((line, n) => {
    const top = top0 + n * lh * k;
    const base = top + (lh * k - (asc + desc)) / 2 + asc;
    s.fillText(upper ? line.toUpperCase() : line, x, base);
  });
  const data = s.getImageData(0, 0, sw, sh).data;
  const c = canvas.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, hgt);
  c.fillStyle = px.color;
  const gap = block >= 10 ? Math.min(3, block * .08) : 0;
  const cut = block >= 14 ? block * .22 : 0;          // clipped corner, like a block font
  for (let yy = 0; yy < sh; yy++) {
    for (let xx = 0; xx < sw; xx++) {
      if (data[(yy * sw + xx) * 4 + 3] < 110) continue;
      const bx = xx * block + gap / 2, by = yy * block + gap / 2, bs = block - gap;
      if (cut) {
        c.beginPath();
        c.moveTo(bx + cut, by); c.lineTo(bx + bs, by); c.lineTo(bx + bs, by + bs - cut);
        c.lineTo(bx + bs - cut, by + bs); c.lineTo(bx, by + bs); c.lineTo(bx, by + cut); c.closePath();
        c.fill();
      } else {
        c.fillRect(bx, by, bs + .5, bs + .5);
      }
    }
  }
}

/** Animate coarse -> fine once. Resolves when the real text is showing. */
export function resolve(el, { step = 85, delay = 0 } = {}) {
  return new Promise((done) => {
    let i = 0;
    const tick = () => {
      drawLevel(el, i);
      if (i >= LEVELS.length) return done();
      i += 1;
      setTimeout(tick, step + (i > 4 ? 25 : 0));
    };
    setTimeout(tick, delay);
  });
}

/** Scroll-driven: p in [0,1] -> level (0 hides nothing: it shows the coarsest blocks). */
export function scrub(el, p) {
  const i = Math.min(LEVELS.length, Math.max(0, Math.floor(p * (LEVELS.length + 1))));
  drawLevel(el, i);
}

/** Hide behind the coarsest level until resolve() is called. */
export function prime(el) { drawLevel(el, 0); }

export function invalidate(el) { if (el._px) { el._px.dirty = true; el._px.level = -2; } }

export const LEVEL_COUNT = LEVELS.length;
