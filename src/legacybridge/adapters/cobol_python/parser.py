"""Deterministic COBOL (fixed-format) parser for the Analyzer.

Extracts what migration needs: PROGRAM-ID, COPY / CALL / PERFORM relations, paragraphs, files,
and a structured data layout (PIC / USAGE / SIGN / OCCURS / REDEFINES / 88-levels) for the
FILE, WORKING-STORAGE, LOCAL-STORAGE and LINKAGE sections. It's not a full COBOL grammar, just a
robust subset that's sufficient for typical batch programs (and fails loudly otherwise).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field


class UnsupportedCobol(Exception):
    """Raised for constructs the adapter can't handle (e.g. OCCURS inside a CALL interface)."""


# ---------------------------------------------------------------- PIC handling


class PicInfo(BaseModel):
    kind: str                 # numeric | alnum | edited
    int_digits: int = 0
    scale: int = 0
    signed: bool = False
    length: int = 0           # display length (chars), excluding a SEPARATE sign
    picture: str = ""


def expand_pic(pic: str) -> str:
    """Expand repeat counts: 'S9(5)V99' -> 'S99999V99'."""
    return re.sub(r"(.)\((\d+)\)", lambda m: m.group(1) * int(m.group(2)), pic.upper())


def pic_info(pic: str) -> PicInfo:
    """Classify a PICTURE string and compute digits/scale/display length."""
    exp = expand_pic(pic)
    if re.fullmatch(r"S?9*(V9*)?", exp) and "9" in exp:
        signed = exp.startswith("S")
        body = exp.lstrip("S")
        int_part, _, frac = body.partition("V")
        return PicInfo(kind="numeric", int_digits=len(int_part), scale=len(frac), signed=signed,
                       length=len(int_part) + len(frac), picture=pic)
    if re.fullmatch(r"[XA9]+", exp) and ("X" in exp or "A" in exp):
        return PicInfo(kind="alnum", length=len(exp), picture=pic)
    # edited: Z * , . + - CR DB $ B 0 / 9
    length = len(exp.replace("V", ""))
    digits_before = exp.split(".")[0] if "." in exp else exp
    scale = len(re.findall(r"[9Z*]", exp.split(".")[1])) if "." in exp else 0
    int_digits = len(re.findall(r"[9Z*+\-$]", digits_before))
    return PicInfo(kind="edited", int_digits=int_digits, scale=scale, signed=bool(re.search(r"[+\-]|CR|DB", exp)),
                   length=length, picture=pic)


# ---------------------------------------------------------------- data model


class DataItem(BaseModel):
    level: int
    name: str                          # 'FILLER' for unnamed
    pic: str | None = None
    usage: str = "DISPLAY"             # DISPLAY | COMP-3 | COMP
    value: str | None = None
    values_88: list[str] = Field(default_factory=list)   # literal values of 88-level children
    conditions: dict[str, list[str]] = Field(default_factory=dict)  # 88 name -> values
    occurs: int | None = None
    redefines: str | None = None
    sign_separate: bool = False        # SIGN LEADING SEPARATE
    justified: bool = False
    children: list["DataItem"] = Field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.pic is None and bool(self.children)

    @property
    def picinfo(self) -> PicInfo | None:
        return pic_info(self.pic) if self.pic else None


class Field_(BaseModel):
    """An elementary field flattened out of a record, with its text-record offset."""

    name: str
    path: list[str]                    # ancestor names, outermost first (01 level first)
    pic: str
    kind: str                          # numeric | alnum | edited
    int_digits: int = 0
    scale: int = 0
    signed: bool = False
    sign_separate: bool = False
    usage: str = "DISPLAY"
    length: int = 0                    # chars in a DISPLAY text record (incl. separate sign)
    offset: int = 0
    values_88: list[str] = Field(default_factory=list)

    @property
    def record(self) -> str:
        return self.path[0] if self.path else self.name


class Paragraph(BaseModel):
    name: str
    performs: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    lines: int = 0


class ModuleInfo(BaseModel):
    name: str
    kind: str                          # program | subprogram | copybook
    path: str
    copies: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    dynamic_calls: list[str] = Field(default_factory=list)
    using: list[str] = Field(default_factory=list)
    paragraphs: list[Paragraph] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)          # SELECT name -> ASSIGN target
    file_records: list[DataItem] = Field(default_factory=list)   # 01 records under FDs
    working_storage: list[DataItem] = Field(default_factory=list)
    linkage: list[DataItem] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    loc: int = 0                       # non-comment code lines

    def linkage_params(self) -> list[DataItem]:
        """Linkage 01 items in PROCEDURE DIVISION USING order."""
        by_name = {d.name: d for d in self.linkage}
        missing = [u for u in self.using if u not in by_name]
        if missing:
            raise UnsupportedCobol(f"{self.name}: USING items not in LINKAGE: {missing}")
        return [by_name[u] for u in self.using]


# ---------------------------------------------------------------- source normalization


def code_lines(text: str) -> list[str]:
    """Fixed format: drop cols 1-6 and comment lines, keep cols 8-72 (indicator col 7)."""
    out = []
    for raw in text.splitlines():
        line = raw.rstrip("\n").expandtabs(4)
        if len(line) < 7:
            continue
        indicator = line[6]
        if indicator in "*/":
            continue
        code = line[7:72]
        if indicator == "-" and out:  # continuation: append (literal continuation simplified)
            out[-1] = out[-1].rstrip() + code.strip().lstrip("'\"")
            continue
        if code.strip().startswith("*>"):
            continue
        out.append(code.rstrip())
    return out


_COPY_RE = re.compile(r"\bCOPY\s+['\"]?([A-Z0-9][A-Z0-9-]*)['\"]?\s*\.", re.IGNORECASE)


def find_copybook(name: str, dirs: Iterable[Path]) -> Path | None:
    for d in dirs:
        for suffix in (".cpy", ".CPY", ".cob", ".cbl", ""):
            p = Path(d) / f"{name}{suffix}"
            if p.exists():
                return p
    return None


def expand_copies(lines: list[str], dirs: list[Path], _depth: int = 0) -> list[str]:
    """Inline COPY statements (recursively)."""
    if _depth > 10:
        raise UnsupportedCobol("COPY nesting too deep")
    out = []
    for line in lines:
        m = _COPY_RE.search(line)
        if not m:
            out.append(line)
            continue
        p = find_copybook(m.group(1).upper(), dirs)
        if p is None:
            raise UnsupportedCobol(f"copybook {m.group(1)} not found in {', '.join(map(str, dirs))}")
        out.extend(expand_copies(code_lines(p.read_text()), dirs, _depth + 1))
    return out


# ---------------------------------------------------------------- data division


def _split_entries(text: str) -> list[str]:
    """Split data-division text into period-terminated entries, respecting quotes."""
    entries, cur, quote = [], [], None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif ch == "." and (i + 1 == len(text) or text[i + 1].isspace()):
            entry = "".join(cur).strip()
            if entry:
                entries.append(entry)
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        entries.append("".join(cur).strip())
    return entries


_TOKEN_RE = re.compile(r"'[^']*'|\"[^\"]*\"|\S+")


def _parse_entry(entry: str) -> DataItem | None:
    toks = _TOKEN_RE.findall(entry)
    if not toks or not toks[0].isdigit():
        return None
    level = int(toks[0])
    i = 1
    name = "FILLER"
    if i < len(toks) and toks[i].upper() not in {"PIC", "PICTURE", "VALUE", "VALUES", "REDEFINES", "OCCURS",
                                                  "USAGE", "COMP-3", "COMP", "SIGN", "JUSTIFIED", "JUST"}:
        name = toks[i].upper()
        i += 1
    item = DataItem(level=level, name=name)
    while i < len(toks):
        t = toks[i].upper()
        if t in ("PIC", "PICTURE"):
            i += 1
            if i < len(toks) and toks[i].upper() == "IS":
                i += 1
            item.pic = toks[i].upper()
        elif t in ("COMP-3", "COMPUTATIONAL-3", "PACKED-DECIMAL"):
            item.usage = "COMP-3"
        elif t in ("COMP", "COMPUTATIONAL", "BINARY", "COMP-5", "COMP-4"):
            item.usage = "COMP"
        elif t == "VALUE" or t == "VALUES":
            i += 1
            if i < len(toks) and toks[i].upper() in ("IS", "ARE"):
                i += 1
            vals = []
            while i < len(toks) and toks[i].upper() not in {"PIC", "PICTURE", "USAGE", "COMP-3", "OCCURS"}:
                if toks[i].upper() not in ("THRU", "THROUGH"):
                    vals.append(toks[i])
                i += 1
            i -= 1
            if level == 88:
                item.values_88 = [_unquote(v) for v in vals]
            else:
                item.value = " ".join(vals)
        elif t == "OCCURS":
            item.occurs = int(toks[i + 1])
            i += 1
        elif t == "REDEFINES":
            item.redefines = toks[i + 1].upper()
            i += 1
        elif t == "SEPARATE":
            item.sign_separate = True
        elif t in ("JUSTIFIED", "JUST"):
            item.justified = True
        i += 1
    return item


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def parse_data_items(lines: list[str]) -> list[DataItem]:
    """Parse a run of data-description entries into a tree of 01/77 items."""
    roots: list[DataItem] = []
    stack: list[DataItem] = []
    for entry in _split_entries("\n".join(lines)):
        item = _parse_entry(entry)
        if item is None:
            continue
        if item.level == 88:
            if stack:
                parent = stack[-1]
                parent.values_88.extend(item.values_88)
                parent.conditions[item.name] = item.values_88
            continue
        if item.level in (1, 77):
            roots.append(item)
            stack = [item]
            continue
        while stack and stack[-1].level >= item.level:
            stack.pop()
        if not stack:
            roots.append(item)
        else:
            stack[-1].children.append(item)
        stack.append(item)
    return roots


def item_display_length(item: DataItem) -> int:
    """Length of an item in a DISPLAY text record (groups = sum of children)."""
    if item.pic:
        pi = pic_info(item.pic)
        n = pi.length + (1 if item.sign_separate else 0)
    else:
        n = sum(item_display_length(c) for c in item.children if not c.redefines)
    return n * (item.occurs or 1)


def flatten(item: DataItem, *, allow_occurs: bool = False, _path: list[str] | None = None,
            _offset: list[int] | None = None) -> list[Field_]:
    """Elementary fields of a record with text offsets. REDEFINES subtrees are skipped."""
    path = (_path or []) + [item.name]
    offset = _offset if _offset is not None else [0]
    if item.occurs and not allow_occurs:
        raise UnsupportedCobol(f"OCCURS in interface record ({item.name}) is not supported")
    if item.pic:
        pi = pic_info(item.pic)
        length = pi.length + (1 if item.sign_separate else 0)
        f = Field_(name=item.name, path=path[:-1], pic=item.pic, kind=pi.kind, int_digits=pi.int_digits,
                   scale=pi.scale, signed=pi.signed, sign_separate=item.sign_separate, usage=item.usage,
                   length=length, offset=offset[0], values_88=item.values_88)
        offset[0] += length * (item.occurs or 1)
        return [f]
    fields: list[Field_] = []
    for child in item.children:
        if child.redefines:
            continue
        fields.extend(flatten(child, allow_occurs=allow_occurs, _path=path, _offset=offset))
    return fields


# ---------------------------------------------------------------- whole program


_PROGRAM_ID = re.compile(r"PROGRAM-ID\.\s*([A-Z0-9][A-Z0-9-]*)", re.IGNORECASE)
_CALL_RE = re.compile(r"\bCALL\s+(['\"])([A-Z0-9-]+)\1", re.IGNORECASE)
_DYN_CALL_RE = re.compile(r"\bCALL\s+([A-Z][A-Z0-9-]*)", re.IGNORECASE)
_PERFORM_RE = re.compile(r"\bPERFORM\s+([A-Z0-9][A-Z0-9-]*)", re.IGNORECASE)
_PERFORM_KEYWORDS = {"UNTIL", "VARYING", "WITH", "TEST", "FOREVER"}
_SELECT_RE = re.compile(r"\bSELECT\s+([A-Z0-9-]+)\s+ASSIGN\s+(?:TO\s+)?(\S+)", re.IGNORECASE)
_USING_RE = re.compile(r"PROCEDURE\s+DIVISION\s+USING\s+(.*?)\.", re.IGNORECASE | re.DOTALL)
_SECTION_RE = re.compile(r"^\s*(FILE|WORKING-STORAGE|LOCAL-STORAGE|LINKAGE)\s+SECTION\s*\.", re.IGNORECASE)

FEATURE_PATTERNS: dict[str, str] = {
    "comp3": r"\bCOMP-3\b|PACKED-DECIMAL",
    "rounded": r"\bROUNDED\b",
    "size_error": r"\bON\s+SIZE\s+ERROR\b",
    "sign_separate": r"SIGN\s+(IS\s+)?LEADING\s+SEPARATE",
    "justified": r"\bJUST(IFIED)?\s+RIGHT\b",
    "string_stmt": r"\bSTRING\b",
    "redefines": r"\bREDEFINES\b",
    "occurs": r"\bOCCURS\b",
    "divide": r"\bDIVIDE\b|\s/\s",
    "remainder": r"\bREMAINDER\b",
    "perform_varying": r"\bPERFORM\s+VARYING\b",
    "evaluate": r"\bEVALUATE\b",
    "ref_mod": r"\([0-9]+:[0-9]+\)",
    "intrinsic": r"(?<![\w-])FUNCTION\s+[A-Z]",
    "goback": r"\bGOBACK\b",
    "return_code": r"\bRETURN-CODE\b",
    "numeric_check": r"\bNOT\s+NUMERIC\b|\bIS\s+NUMERIC\b",
    "file_io": r"\bREAD\s+[A-Z]",
}


def detect_features(code: str, data_items: list[DataItem]) -> list[str]:
    """Tags describing tricky semantics present in a module (used for summaries/lessons)."""
    feats = {name for name, pat in FEATURE_PATTERNS.items() if re.search(pat, code, re.IGNORECASE)}

    def walk(items: list[DataItem]) -> Iterable[DataItem]:
        for it in items:
            yield it
            yield from walk(it.children)

    all_items = list(walk(data_items))
    pics = [pic_info(i.pic) for i in all_items if i.pic]
    if any(p.kind == "edited" for p in pics):
        feats.add("edited_pic")
    if any(p.kind == "numeric" and p.signed for p in pics):
        feats.add("signed")
    if any(p.kind == "numeric" and not p.signed for p in pics):
        feats.add("unsigned")
    if any("DATE" in i.name for i in all_items):
        feats.add("dates")
    if re.search(r"\bCOMPUTE\b", code, re.IGNORECASE) and not re.search(r"\bON\s+SIZE\s+ERROR\b", code, re.I):
        feats.add("silent_truncation")
    elif re.search(r"\bCOMPUTE\b", code, re.IGNORECASE):
        feats.add("truncation")
    # DISPLAY of a numeric elementary item (GnuCOBOL prints sign + decimal point)
    numeric_names = {i.name for i in all_items if i.pic and pic_info(i.pic).kind == "numeric"}
    for m in re.finditer(r"\bDISPLAY\s+(.*)", code, re.IGNORECASE):
        if any(tok.upper() in numeric_names for tok in re.findall(r"[A-Z0-9-]+", m.group(1), re.I)):
            feats.add("display_numeric")
    return sorted(feats)


def parse_program(path: Path, copy_dirs: list[Path]) -> ModuleInfo:
    """Parse one COBOL program file."""
    text = path.read_text()
    lines = code_lines(text)
    joined = "\n".join(lines)
    m = _PROGRAM_ID.search(joined)
    if not m:
        raise UnsupportedCobol(f"{path}: no PROGRAM-ID")
    name = m.group(1).upper()
    copies = [c.upper() for c in _COPY_RE.findall(joined)]
    expanded = expand_copies(lines, copy_dirs)

    # split divisions
    def index_of(pattern: str) -> int:
        for i, ln in enumerate(expanded):
            if re.search(pattern, ln, re.IGNORECASE):
                return i
        return -1

    data_i = index_of(r"^\s*DATA\s+DIVISION")
    proc_i = index_of(r"^\s*PROCEDURE\s+DIVISION")
    if proc_i < 0:
        raise UnsupportedCobol(f"{path}: no PROCEDURE DIVISION")
    env_lines = expanded[: data_i if data_i >= 0 else proc_i]
    data_lines = expanded[data_i + 1 : proc_i] if data_i >= 0 else []
    proc_lines = expanded[proc_i:]

    sections: dict[str, list[str]] = {"FILE": [], "WORKING-STORAGE": [], "LOCAL-STORAGE": [], "LINKAGE": []}
    current = None
    for ln in data_lines:
        sm = _SECTION_RE.match(ln)
        if sm:
            current = sm.group(1).upper()
            continue
        if current:
            sections[current].append(ln)
    file_lines = [ln for ln in sections["FILE"] if not re.match(r"^\s*FD\s", ln, re.IGNORECASE)]
    file_records = parse_data_items(file_lines)
    ws = parse_data_items(sections["WORKING-STORAGE"] + sections["LOCAL-STORAGE"])
    linkage = parse_data_items(sections["LINKAGE"])

    proc_text = "\n".join(proc_lines)
    um = _USING_RE.search(proc_text)
    using = [u.upper() for u in re.findall(r"[A-Z0-9-]+", um.group(1), re.I)
             if u.upper() not in ("BY", "REFERENCE", "VALUE", "CONTENT")] if um else []

    # paragraphs: a name alone on a line in Area A, ending with a period
    paragraphs: list[Paragraph] = []
    cur: Paragraph | None = None
    for ln in proc_lines[1:]:
        pm = re.match(r"^([A-Z0-9][A-Z0-9-]*)(\s+SECTION)?\s*\.\s*$", ln, re.IGNORECASE)
        if pm and ln[:1] != " ":  # starts in Area A (column 8)
            cur = Paragraph(name=pm.group(1).upper())
            paragraphs.append(cur)
            continue
        if cur is None:
            cur = Paragraph(name="(MAIN)")
            paragraphs.append(cur)
        cur.lines += 1
        for target in _PERFORM_RE.findall(ln):
            if target.upper() not in _PERFORM_KEYWORDS and not target.isdigit():
                cur.performs.append(target.upper())
        for _, target in _CALL_RE.findall(ln):
            cur.calls.append(target.upper())

    calls = sorted({c.upper() for _, c in _CALL_RE.findall(proc_text)})
    dyn = sorted({d.upper() for d in _DYN_CALL_RE.findall(proc_text)} - {"USING"})
    files = {s.upper(): a.upper().rstrip(".") for s, a in _SELECT_RE.findall("\n".join(env_lines))}
    feats = detect_features("\n".join(expanded), file_records + ws + linkage)
    return ModuleInfo(
        name=name, kind="subprogram" if using else "program", path=str(path), copies=copies, calls=calls,
        dynamic_calls=dyn, using=using, paragraphs=paragraphs, files=files, file_records=file_records,
        working_storage=ws, linkage=linkage, features=feats, loc=len(lines),
    )


def parse_copybook(path: Path) -> ModuleInfo:
    lines = code_lines(path.read_text())
    items = parse_data_items(lines)
    return ModuleInfo(name=path.stem.upper(), kind="copybook", path=str(path),
                      working_storage=items, features=detect_features("\n".join(lines), items), loc=len(lines))
