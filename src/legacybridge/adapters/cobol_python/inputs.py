"""Deterministic test-input generation for COBOL modules (property, random, mutation) plus
validation/canonicalization of inputs and conversion of LLM-proposed cases into stdin text.

All generators work on `iofmt.Layout`, so produced inputs are always well-formed for the
driver (subprograms) or the program's input file (mains). Everything is seeded.
"""

from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .iofmt import IOField, Layout, Record, printable
from .parser import code_lines

MAX_LINES = {"call": 20, "file": 40}
ID_HINTS = ("-ID", "KEY", "ACCT-NO", "ACCOUNT")
NAME_WORDS = ["ALICE SMITH", "BOB JONES", "CAROL WU", "DAN O'NEIL", "EVE ADAMS", "RENT", "PAYROLL",
              "ATM WITHDRAWAL", "GROCERIES", "TRANSFER IN", "INTEREST", "FEE REVERSAL"]
SPECIAL_DATES = [19000101, 19000228, 19000301, 19991231, 20000229, 20230228, 20240229, 20241231,
                 20991231, 20230229, 20231301, 20230431, 20230100, 18991231, 0]

_NUM_LIT = re.compile(r"(?<![\w\-.])[+-]?\d+(?:\.\d+)?(?![\w\-])")
_STR_LIT = re.compile(r"'([^']{1,3})'")
_CMP_RE = re.compile(r"\b([A-Z][A-Z0-9-]*)\s+(?:NOT\s+)?(?:=|EQUAL(?:\s+TO)?)\s+'([^']*)'", re.I)
_EVAL_RE = re.compile(r"\bEVALUATE\s+([A-Z][A-Z0-9-]*)\s*\n(.*?)END-EVALUATE", re.I | re.S)
_NUM_CMP_RE = re.compile(
    r"\b([A-Z][A-Z0-9-]*)(?:\([^)]*\))?\s+(?:IS\s+)?(?:NOT\s+)?"
    r"(?:<=|>=|<|>|=|LESS\s+THAN(?:\s+OR\s+EQUAL(?:\s+TO)?)?|GREATER\s+THAN(?:\s+OR\s+EQUAL(?:\s+TO)?)?|EQUAL(?:\s+TO)?)"
    r"\s+([+-]?\d+(?:\.\d+)?)(?![\w\-])", re.I)
_MOVE_RE = re.compile(r"\bMOVE[ \t]+([A-Z][A-Z0-9-]*)[ \t]+TO[ \t]+([A-Z][A-Z0-9-]*(?:[ \t]+[A-Z][A-Z0-9-]*)*)[ \t]*$",
                      re.I | re.M)
_NOT_FIELDS = {"ROUNDED", "GIVING", "REMAINDER", "TO", "FROM", "BY", "INTO", "UNTIL", "VARYING", "WHEN", "SPACES",
               "ZEROS", "ZERO", "SPACE", "OR", "AND", "NOT"}


@dataclass
class Literals:
    nums: list[Decimal] = field(default_factory=list)
    strs: list[str] = field(default_factory=list)
    by_field: dict[str, list[str]] = field(default_factory=dict)   # FIELD -> literals compared to it
    num_by_field: dict[str, list[Decimal]] = field(default_factory=dict)  # FIELD -> numeric thresholds


def harvest_literals(source: str) -> Literals:
    """Numeric/short string literals from the PROCEDURE DIVISION (boundary candidates), and the
    string literals each field is compared against (IF F = 'X', EVALUATE F WHEN 'X')."""
    lines = code_lines(source)
    start = next((i for i, ln in enumerate(lines) if re.search(r"PROCEDURE\s+DIVISION", ln, re.I)), 0)
    text = "\n".join(lines[start:])
    nums = sorted({Decimal(m) for m in _NUM_LIT.findall(text)})
    strs = sorted({s for s in _STR_LIT.findall(text)})
    by_field: dict[str, set[str]] = {}
    for m in _CMP_RE.finditer(text):
        by_field.setdefault(m.group(1).upper(), set()).add(m.group(2))
    for m in _EVAL_RE.finditer(text):
        name, body = m.group(1).upper(), m.group(2)
        body = body.split("EVALUATE", 1)[0]  # don't descend into nested EVALUATEs
        for lit in re.findall(r"WHEN\s+'([^']*)'", body, re.I):
            by_field.setdefault(name, set()).add(lit)
    num_by_field: dict[str, set[Decimal]] = {}
    for m in _NUM_CMP_RE.finditer(text):
        if m.group(1).upper() not in _NOT_FIELDS:
            num_by_field.setdefault(m.group(1).upper(), set()).add(Decimal(m.group(2)))
    # thresholds on a copy (MOVE IN-X TO WS-X ... IF WS-X > 5) also apply to the source field
    for m in _MOVE_RE.finditer(text):
        src = m.group(1).upper()
        for dst in m.group(2).upper().split():
            for table in (by_field, num_by_field):
                if dst in table and src not in _NOT_FIELDS:
                    table.setdefault(src, set()).update(table[dst])
    return Literals(nums, strs, {k: sorted(v) for k, v in by_field.items()},
                    {k: sorted(v) for k, v in num_by_field.items()})


def _is_date(f: IOField) -> bool:
    return f.numeric and "DATE" in f.name and f.f.int_digits == 8 and f.f.scale == 0


def _is_id(f: IOField) -> bool:
    return not f.numeric and any(h in f.name for h in ID_HINTS) and f.width >= 4


def _ulp(f: IOField) -> Decimal:
    return Decimal(1).scaleb(-f.f.scale)


def _max(f: IOField) -> Decimal:
    return Decimal(10) ** f.f.int_digits - _ulp(f)


def _fits(f: IOField, v: Decimal) -> bool:
    if v < 0 and not f.f.signed:
        return False
    return abs(v) <= _max(f) and v == v.quantize(_ulp(f), rounding="ROUND_DOWN")


def _rand_date(rng: random.Random, lo: int = 2015, hi: int = 2030) -> int:
    d = dt.date(lo, 1, 1) + dt.timedelta(days=rng.randrange((dt.date(hi, 12, 31) - dt.date(lo, 1, 1)).days))
    return int(d.strftime("%Y%m%d"))


class InputGenerator:
    """Field- and record-level value strategies for one module."""

    def __init__(self, layout: Layout, literals: Literals, rng: random.Random):
        self.layout = layout
        self.nums, self.strs, self.by_field = literals.nums, literals.strs, literals.by_field
        self.num_by_field = literals.num_by_field
        self.rng = rng
        self.id_pool: dict[int, list[str]] = {}
        self.case_ids: list[str] = []      # IDs introduced by leading records of the current case

    def codes(self, f: IOField) -> list[str]:
        """Meaningful codes for an alphanumeric field: 88 values, then literals it's compared to."""
        vals = list(f.f.values_88)
        vals += [v for v in self.by_field.get(f.name, []) if len(v) <= f.width and v not in vals]
        return vals

    # ------------------------------------------------------------ field values
    def ids(self, width: int) -> list[str]:
        if width not in self.id_pool:
            digits = max(1, min(4, width - 2))
            prefix = "AC"[: max(0, width - digits)]
            self.id_pool[width] = [(prefix + str(i).zfill(digits))[:width] for i in (1, 2, 3, 17)]
        return self.id_pool[width]

    def typical(self, f: IOField, rec: Record) -> Any:
        rng = self.rng
        if rec.type_field() is f:
            return f.f.values_88[0]
        if not f.numeric:
            codes = self.codes(f)
            if codes:
                return codes[0] if rng.random() < 0.4 else rng.choice(codes)
            if _is_id(f):
                if self.case_ids and rec is not self.layout.records[0] and rng.random() < 0.85:
                    return rng.choice([i for i in self.case_ids if len(i) <= f.width] or self.ids(f.width))
                return rng.choice(self.ids(f.width))
            if f.width == 1:
                ones = [s for s in self.strs if len(s) == 1 and s.strip()]
                return rng.choice(ones) if ones else rng.choice("ABCXYZ")
            words = [w for w in NAME_WORDS if len(w) <= f.width] or ["X" * f.width]
            return rng.choice(words)
        if _is_date(f):
            return _rand_date(rng)
        if any(h in f.name for h in ("RETURN-CODE", "-RC", "RESULT")) and not f.f.scale:
            return 0
        if "RATE" in f.name:
            hi = min(_max(f), Decimal("0.15") if f.f.int_digits <= 1 else Decimal(15))
            return (Decimal(rng.randint(1, max(1, int(hi / _ulp(f))))) * _ulp(f))
        mag_digits = min(f.f.int_digits, rng.choice([1, 2, 3, 3, 4, 5]))
        v = Decimal(rng.randint(0, 10 ** mag_digits - 1)) + (Decimal(rng.randint(0, 10 ** f.f.scale - 1)) * _ulp(f)
                                                            if f.f.scale else 0)
        if f.f.signed and rng.random() < 0.15:
            v = -v
        return v

    def boundaries(self, f: IOField, rec: Record) -> list[Any]:
        if rec.type_field() is f:
            return []
        if not f.numeric:
            vals: list[Any] = self.codes(f)
            if vals or f.width == 1:
                vals += ["?", " " * f.width]
                vals += [s for s in self.strs if len(s) <= f.width and s not in vals]
            elif _is_id(f):
                vals += self.ids(f.width)[:2] + ["Z" * f.width]
            else:
                vals += [" " * f.width, "X" * f.width]
            return vals
        if _is_date(f):
            return list(SPECIAL_DATES)
        u = _ulp(f)
        cand = self.thresholds(f)            # this field's own comparison thresholds first
        cand += [Decimal(0), u, _max(f), _max(f) - u, Decimal(10) ** (f.f.int_digits // 2)]
        for lit in self.nums:
            cand += [lit, lit - u, lit + u]
        if f.f.signed:
            cand += [-c for c in cand if c > 0][:8]
        out, seen = [], set()
        for c in cand:
            if _fits(f, c) and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def thresholds(self, f: IOField) -> list[Decimal]:
        """lit-ulp, lit, lit+ulp for every literal the program compares this field against."""
        if not f.numeric:
            return []
        u = _ulp(f)
        out = []
        for lit in self.num_by_field.get(f.name, []):
            out += [c for c in (lit - u, lit, lit + u) if _fits(f, c) and c not in out]
        return out

    def random_value(self, f: IOField, rec: Record) -> Any:
        rng = self.rng
        if rec.type_field() is f:
            return f.f.values_88[0]
        if not f.numeric:
            codes = self.codes(f)
            if codes and rng.random() < 0.85:
                return rng.choice(codes)
            if _is_id(f) and rng.random() < 0.85:
                return rng.choice(self.ids(f.width))
            alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, f.width)))
        if _is_date(f):
            return _rand_date(rng, 1900, 2099) if rng.random() < 0.8 else rng.randint(0, 99999999)
        ndig = rng.randint(0, f.f.int_digits)
        v = Decimal(rng.randint(0, 10 ** ndig - 1) if ndig else 0)
        if f.f.scale:
            v += Decimal(rng.randint(0, 10 ** f.f.scale - 1)) * _ulp(f)
        if f.f.signed and rng.random() < 0.3:
            v = -v
        return v

    # ------------------------------------------------------------ records / cases
    def record(self, rec: Record, overrides: dict[str, Any] | None = None, random_: bool = False) -> str:
        vals = {f.key: (self.random_value(f, rec) if random_ else self.typical(f, rec)) for f in rec.fields}
        vals.update(overrides or {})
        return rec.format(vals)

    def sequence(self, n_lines: int, make) -> list[str]:
        """Lines for a test case; for multi-record files: a leading record then mixed followers."""
        recs = self.layout.records
        self.case_ids = []
        if self.layout.mode == "call" or len(recs) == 1:
            return [make(recs[0]) for _ in range(n_lines)]
        lines: list[str] = []
        id_fields = [f for f in recs[0].fields if _is_id(f)]
        while len(lines) < n_lines:
            lead = make(recs[0])
            lines.append(lead)
            texts = recs[0].split(lead)
            self.case_ids += [texts[recs[0].fields.index(f)].rstrip() for f in id_fields]
            for _ in range(self.rng.randint(1, max(1, n_lines - len(lines)))):
                rec = self.rng.choice(recs[1:] + recs[:1]) if self.rng.random() < 0.2 else self.rng.choice(recs[1:])
                lines.append(make(rec))
        return lines[: max(n_lines, 1)]


def property_cases(gen: InputGenerator, n: int) -> list[str]:
    """One-factor-at-a-time boundary cases spread over all (field, boundary) pairs."""
    pairs = [(r, f, b) for r in gen.layout.records for f in r.fields for b in gen.boundaries(f, r)]
    gen.rng.shuffle(pairs)
    hot = [(r, f, b) for r in gen.layout.records for f in r.fields for b in gen.thresholds(f)]
    gen.rng.shuffle(hot)
    pairs = hot + [x for x in pairs if x not in hot]       # comparison thresholds get covered first
    cases, k = [], 0
    for _ in range(n):
        lines = []
        per_case = 1 if gen.layout.mode == "call" else 0
        size = gen.rng.randint(1, 3) if gen.layout.mode == "call" else gen.rng.randint(2, 8)
        for line in gen.sequence(size, lambda rec: gen.record(rec)):
            lines.append(line)
        # inject boundaries into a few lines of this case
        for i in range(len(lines)):
            if not pairs or (per_case == 0 and gen.rng.random() < 0.5):
                continue
            rec, f, b = pairs[k % len(pairs)]
            k += 1
            target = gen.layout.record_for(lines[i])
            if target is not rec:
                continue
            texts = rec.split(lines[i])
            texts[rec.fields.index(f)] = f.format(b)
            lines[i] = ("|" if rec.separated else "").join(texts)
        cases.append("\n".join(lines) + "\n")
    return cases


def random_cases(gen: InputGenerator, n: int) -> list[str]:
    cases = []
    for _ in range(n):
        size = gen.rng.randint(1, 4) if gen.layout.mode == "call" else gen.rng.randint(1, 14)
        lines = gen.sequence(size, lambda rec: gen.record(rec, random_=True))
        if gen.layout.mode == "file" and gen.rng.random() < 0.15:  # unknown record type
            lines.insert(gen.rng.randrange(len(lines) + 1), "?" + "9" * (gen.layout.max_length - 1))
        cases.append("\n".join(lines) + "\n")
    return cases


def volume_cases(gen: InputGenerator, n: int) -> list[str]:
    """Long inputs: for multi-record files ONE leading record followed by many detail records
    (exercises page breaks, table limits, counters); for subprograms many CALLs in a row (state
    carried between calls)."""
    cases = []
    limit = MAX_LINES[gen.layout.mode]
    recs = gen.layout.records
    for _ in range(n):
        size = gen.rng.randint(limit // 2, limit - 1)
        mixed = gen.rng.random() < 0.5
        make = lambda rec: gen.record(rec, random_=mixed)          # noqa: E731
        if gen.layout.mode == "file" and len(recs) > 1:
            lines = [make(recs[0])] + [make(gen.rng.choice(recs[1:])) for _ in range(size - 1)]
        else:
            lines = gen.sequence(size, make)
        cases.append("\n".join(lines) + "\n")
    return cases


def mutation_cases(gen: InputGenerator, seeds: list[str], n: int) -> list[str]:
    """Adversarial edits of existing inputs: boundary values, literal +-1, sign flips, line ops."""
    rng = gen.rng
    out: list[str] = []
    seeds = [s for s in seeds if s.strip()]
    if not seeds:
        return out
    for _ in range(n * 3):
        if len(out) >= n:
            break
        lines = rng.choice(seeds).rstrip("\n").split("\n")
        op = rng.random()
        if op < 0.12 and len(lines) > 1:
            lines.insert(rng.randrange(len(lines)), rng.choice(lines))          # duplicate
        elif op < 0.2 and len(lines) > 1:
            del lines[rng.randrange(len(lines))]                                 # drop
        elif op < 0.26 and len(lines) > 1:
            i = rng.randrange(len(lines) - 1)
            lines[i], lines[i + 1] = lines[i + 1], lines[i]                      # swap
        else:
            for _ in range(rng.randint(1, 2)):
                i = rng.randrange(len(lines))
                rec = gen.layout.record_for(lines[i])
                if rec is None:
                    continue
                texts = rec.split(lines[i])
                j = rng.randrange(len(rec.fields))
                f = rec.fields[j]
                choices = gen.boundaries(f, rec)
                hot = gen.thresholds(f)
                if hot and rng.random() < 0.5:
                    choices = hot
                elif f.numeric and rng.random() < 0.3:
                    cur = f.parse(texts[j])
                    if isinstance(cur, Decimal):
                        choices = [c for c in (-cur, cur + _ulp(f), cur - _ulp(f), cur * 10) if _fits(f, c)] or choices
                if not choices:
                    continue
                try:
                    texts[j] = f.format(rng.choice(choices))
                except ValueError:
                    continue
                lines[i] = ("|" if rec.separated else "").join(texts)
        text = "\n".join(lines) + "\n"
        if text not in out and text not in seeds:
            out.append(text)
    return out


# ---------------------------------------------------------------- validation / LLM cases

def canonical(layout: Layout, text: str) -> str:
    """Normalize line endings; pad driver lines to full width; ensure a trailing newline."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").split("\n") if text.strip() else []
    if layout.mode == "call":
        n = layout.records[0].length
        lines = [ln.ljust(n) if len(ln) < n else ln for ln in lines]
    return "\n".join(lines) + ("\n" if lines else "")


def validate(layout: Layout, text: str) -> str | None:
    """None if `text` is an acceptable stdin for the module, else the reason."""
    if not printable(text.replace("\n", "")):
        return "only printable ASCII is allowed (no tabs or control characters)"
    lines = text.rstrip("\n").split("\n") if text.strip() else []
    if layout.mode == "call" and not lines:
        return "at least one CALL line is required"
    if len(lines) > MAX_LINES[layout.mode]:
        return f"at most {MAX_LINES[layout.mode]} lines"
    for no, line in enumerate(lines, 1):
        if layout.mode == "call":
            if reason := layout.records[0].check(line, strict_numeric=True):
                return f"line {no}: {reason}"
        elif len(line) > layout.max_length:
            # GnuCOBOL splits over-long LINE SEQUENTIAL lines and drops a character
            return f"line {no}: longer than the {layout.max_length}-char record"
    return None


def case_from_llm(layout: Layout, obj: dict[str, Any]) -> str:
    """{'records': [{'record': name?, 'fields': {FIELD: value}}]} -> stdin text (ValueError if bad)."""
    recs = obj.get("records")
    if not isinstance(recs, list) or not recs:
        raise ValueError("'records' must be a non-empty list")
    lines = []
    for item in recs:
        if not isinstance(item, dict):
            raise ValueError("each record must be an object")
        fields = item.get("fields", {k: v for k, v in item.items() if k != "record"})
        if not isinstance(fields, dict):
            raise ValueError("'fields' must be an object")
        rec = layout.record_by_name(item.get("record")) if layout.mode == "file" else layout.records[0]
        lines.append(rec.format({str(k).upper(): v for k, v in fields.items()}))
    return "\n".join(lines) + "\n"


def llm_instructions(layout: Layout) -> str:
    """How the LLM must express test cases for this module."""
    if layout.mode == "call":
        head = ("Each test case is a sequence of CALLs to the subprogram (state in WORKING-STORAGE persists "
                "between CALLs within one case). Give every CALL as one record with ALL parameter fields; "
                "output-only fields still need a starting value (it can matter if the program leaves them "
                "untouched).")
    else:
        head = ("Each test case is the program's input file: a list of records, each naming its record "
                "layout in 'record'. For numeric fields you may pass {\"raw\": \"<exact text>\"} to test "
                "invalid data (e.g. spaces) - the raw text must have exactly the field width.")
    lay = "\n".join(f"Record {r.name}:\n{r.describe()}" for r in layout.records)
    return (f"{head}\nNumbers are decimal strings like \"-1234.56\" (no thousands separators); they must fit "
            f"the PIC. Dates are YYYYMMDD numbers. Text longer than the field is rejected.\n{lay}\n"
            'JSON shape: {"cases": [{"name": "...", "why": "...", "records": [{"record": "<RECORD>", '
            '"fields": {"<FIELD>": "<value>"}}]}]}')
