// A small syntax highlighter for COBOL and Python, themed with the site palette (see .code in base.css).
// Output: one <span class="cl"> per source line with a hanging indent, so long lines wrap under
// their own indentation instead of the gutter.

const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

const COBOL_KW = new Set(`ACCEPT ADD AFTER ALL ALSO AND ASCENDING ASSIGN AT BEFORE BINARY BY CALL CANCEL CLOSE COMP COMP-3
COMPUTE CONTINUE COPY CORRESPONDING DATA DELETE DELIMITED DEPENDING DESCENDING DISPLAY DIVIDE DIVISION ELSE END END-CALL
END-COMPUTE END-EVALUATE END-IF END-PERFORM END-READ END-STRING END-WRITE ENVIRONMENT EQUAL ERROR EVALUATE EXIT FD
FILE FILE-CONTROL FROM GIVING GOBACK GO GREATER IDENTIFICATION IF IN INITIALIZE INPUT INPUT-OUTPUT INSPECT INTO IS
LESS LINKAGE MOVE MULTIPLY NOT OCCURS OF ON OPEN OR OTHER OUTPUT PERFORM PIC PICTURE PROCEDURE PROGRAM-ID READ
REDEFINES REMAINDER REPLACING ROUNDED RUN SECTION SELECT SET SIZE SPACE SPACES STOP STRING SUBTRACT TALLYING THAN
THEN THROUGH THRU TIMES TO UNTIL UNSTRING USAGE USING VALUE VALUES VARYING WHEN WITH WORKING-STORAGE WRITE ZERO
ZEROES ZEROS TRUE FALSE`.split(/\s+/));

const PY_KW = new Set(`and as assert async await break class continue def del elif else except finally for from global if
import in is lambda nonlocal not or pass raise return try while with yield match case`.split(/\s+/));
const PY_CONST = new Set(["True", "False", "None"]);
const PY_BUILTIN = new Set(`abs all any bool dict enumerate float int len list max min print range round set sorted str sum
tuple zip isinstance getattr setattr super`.split(/\s+/));

function cobolLine(raw) {
  // fixed format: 1-6 sequence area, 7 indicator, 8-72 code
  const seq = raw.slice(0, 6), ind = raw[6] || "", body = raw.slice(7);
  if (ind === "*" || ind === "/" || /^\s*\*>/.test(body)) return `<span class="t-cm">${esc(raw)}</span>`;
  let out = esc(seq) + esc(ind);
  const re = /('[^']*'?|"[^"]*"?)|(\bPIC(?:TURE)?\s+[^\s.]+(?:\.[^\s]+)?)|(\b\d+(?:\.\d+)?\b)|([A-Z0-9][A-Z0-9-]*)|(\s+|.)/gi;
  let m, first = true;
  const areaA = /^\s{0,3}\S/.test(body);
  while ((m = re.exec(body))) {
    const [tok, str, pic, num, word] = m;
    if (str) out += `<span class="t-str">${esc(str)}</span>`;
    else if (pic) out += pic.replace(/^(PIC(?:TURE)?)(\s+)(.*)$/i, (_, k, s, p) => `<span class="t-kw">${k}</span>${s}<span class="t-type">${esc(p)}</span>`);
    else if (num) out += first && /^\d{2}$/.test(num) ? `<span class="t-lvl">${num}</span>` : `<span class="t-num">${num}</span>`;
    else if (word) {
      const W = word.toUpperCase();
      if (COBOL_KW.has(W) || W.startsWith("END-")) out += `<span class="t-kw">${esc(word)}</span>`;
      else if (first && areaA) out += `<span class="t-fn">${esc(word)}</span>`;     // paragraph / section name
      else out += `<span class="t-id">${esc(word)}</span>`;
    } else out += esc(tok);
    if (tok.trim()) first = false;
  }
  return out;
}

function pyLine(raw) {
  const re = /(#.*$)|("""|'''|f?"(?:[^"\\]|\\.)*"?|f?'(?:[^'\\]|\\.)*'?)|(@\w+)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)|(\s+|.)/g;
  let m, out = "", prev = "";
  while ((m = re.exec(raw))) {
    const [tok, cm, str, deco, num, word] = m;
    if (cm) out += `<span class="t-cm">${esc(cm)}</span>`;
    else if (str) out += `<span class="t-str">${esc(str)}</span>`;
    else if (deco) out += `<span class="t-fn">${esc(deco)}</span>`;
    else if (num) out += `<span class="t-num">${num}</span>`;
    else if (word) {
      if (PY_KW.has(word)) out += `<span class="t-kw">${word}</span>`;
      else if (PY_CONST.has(word)) out += `<span class="t-num">${word}</span>`;
      else if (prev === "def" || prev === "class") out += `<span class="t-fn">${word}</span>`;
      else if (PY_BUILTIN.has(word)) out += `<span class="t-type">${word}</span>`;
      else out += `<span class="t-id">${word}</span>`;
    } else out += esc(tok);
    if (tok.trim()) prev = word || tok.trim();
  }
  return out;
}

/** HTML for `src` in `lang` ("cobol" | "python"); `hl` = set of 1-based line numbers to emphasise. */
export function highlight(src, lang, { hl = new Set() } = {}) {
  const lines = String(src ?? "").replace(/\t/g, "    ").replace(/\s+$/, "").split("\n");
  const fn = lang === "python" ? pyLine : cobolLine;
  return lines.map((raw, i) => {
    const lead = raw.match(/^ */)[0].length;
    const cls = "cl" + (hl.has(i + 1) ? " hl" : "");
    return `<span class="${cls}" style="--ind:${Math.min(lead, 40)}">${fn(raw) || " "}</span>`;
  }).join("");
}

/** Render into a <pre class="code"> element. */
export function paint(pre, src, lang, opts) {
  pre.innerHTML = highlight(src, lang, opts);
  pre.dataset.lang = lang;
}
