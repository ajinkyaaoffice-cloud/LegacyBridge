"""Text I/O layouts shared by the COBOL driver generator, the Python harness generator and the
test-input generators. One definition, so both sides of a differential test agree byte-for-byte.

* CALLed subprograms: one stdin line per CALL. Every elementary field of every USING parameter,
  in order, separated by '|'. Numerics are digits only (implied decimal point), with a leading
  '+'/'-' when the field is signed. Alphanumerics are fixed width. After the CALL the driver
  prints the same layout with the values the subprogram left in the parameters.
* Main programs: stdin is the program's input file (LINE SEQUENTIAL); each line is one of the
  FD record layouts.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .parser import Field_, ModuleInfo, UnsupportedCobol, flatten

SEP = "|"
PRINTABLE = set(string.printable) - set("\t\n\r\x0b\x0c")


@dataclass
class IOField:
    """One elementary field in a text layout."""

    param: str          # 01-level record / parameter name
    f: Field_
    key: str            # unique name used in dicts / LLM JSON
    offset: int = 0     # offset in the text line

    @property
    def name(self) -> str:
        return self.f.name

    @property
    def numeric(self) -> bool:
        return self.f.kind == "numeric"

    @property
    def width(self) -> int:
        if self.numeric:
            return self.f.int_digits + self.f.scale + (1 if self.f.signed else 0)
        return self.f.length

    def qualified(self) -> str:
        """COBOL reference with full qualification: F OF G OF REC."""
        chain = [self.f.name] + list(reversed(self.f.path))
        return " OF ".join(chain)

    def text_pic(self) -> str:
        """PICTURE of the text representation."""
        if not self.numeric:
            return f"X({self.width})"
        pic = ("S" if self.f.signed else "") + (f"9({self.f.int_digits})" if self.f.int_digits else "")
        pic += f"V9({self.f.scale})" if self.f.scale else ""
        return pic + (" SIGN LEADING SEPARATE" if self.f.signed else "")

    def describe(self) -> str:
        if self.numeric:
            kind = (f"numeric {'signed' if self.f.signed else 'unsigned'} {self.f.int_digits} integer digits"
                    + (f", {self.f.scale} decimals" if self.f.scale else ""))
        else:
            kind = f"text, {self.width} chars"
        extra = f"; valid codes: {', '.join(repr(v) for v in self.f.values_88)}" if self.f.values_88 else ""
        return f"{self.key} (PIC {self.f.pic}{' ' + self.f.usage if self.f.usage != 'DISPLAY' else ''}): {kind}{extra}"

    # ---------------------------------------------------------------- text <-> value
    def format(self, value: object) -> str:
        """Value -> exact text for this field. Raises ValueError if it can't be represented."""
        if isinstance(value, dict) and "raw" in value:
            raw = str(value["raw"])
            if len(raw) != self.width:
                raise ValueError(f"{self.key}: raw text must be exactly {self.width} chars")
            return raw
        if not self.numeric:
            s = "" if value is None else str(value)
            if len(s) > self.width:
                raise ValueError(f"{self.key}: {s!r} longer than {self.width} chars")
            return s.ljust(self.width)
        try:
            d = Decimal(str(value if value not in (None, "") else 0).strip())
        except InvalidOperation as exc:
            raise ValueError(f"{self.key}: {value!r} is not a number") from exc
        if d < 0 and not self.f.signed:
            raise ValueError(f"{self.key}: negative value for unsigned field")
        q = d.copy_abs().quantize(Decimal(1).scaleb(-self.f.scale), rounding="ROUND_DOWN")
        if q >= Decimal(10) ** self.f.int_digits:
            raise ValueError(f"{self.key}: {value} has more than {self.f.int_digits} integer digits")
        digits = str(int(q.scaleb(self.f.scale))).rjust(self.f.int_digits + self.f.scale, "0")
        return (("-" if d < 0 and q != 0 else "+") + digits) if self.f.signed else digits

    def check(self, text: str, strict_numeric: bool) -> str | None:
        """Reason if `text` is not an acceptable value for this field, else None."""
        if len(text) != self.width:
            return f"{self.key}: expected {self.width} chars, got {len(text)}"
        if self.numeric and strict_numeric:
            body = text
            if self.f.signed:
                if text[:1] not in "+-":
                    return f"{self.key}: signed field must start with '+' or '-': {text!r}"
                body = text[1:]
            if not (body.isascii() and body.isdigit()) and body:
                return f"{self.key}: digits expected, got {text!r}"
        return None

    def parse(self, text: str) -> Decimal | str:
        if not self.numeric:
            return text
        sign = -1 if self.f.signed and text[:1] == "-" else 1
        body = text[1:] if self.f.signed else text
        try:
            return (Decimal(int(body or "0")) * sign).scaleb(-self.f.scale)
        except ValueError:
            return text


@dataclass
class Record:
    """A fixed text layout (one FD record, or one CALL line)."""

    name: str
    fields: list[IOField]
    separated: bool = False

    @property
    def length(self) -> int:
        return sum(f.width for f in self.fields) + (len(self.fields) - 1 if self.separated else 0)

    def type_field(self) -> IOField | None:
        """Leading 1-char field with a single 88 value: a record-type tag ('A', 'T', ...)."""
        f = self.fields[0] if self.fields else None
        if f and not f.numeric and f.width == 1 and len(f.f.values_88) == 1:
            return f
        return None

    def format(self, values: dict[str, object]) -> str:
        unknown = set(values) - {f.key for f in self.fields} - {f.name for f in self.fields}
        if unknown:
            raise ValueError(f"unknown field(s) for {self.name}: {sorted(unknown)}")
        parts = []
        for f in self.fields:
            v = values.get(f.key, values.get(f.name))
            if v is None and (tf := self.type_field()) is f:
                v = f.f.values_88[0]
            parts.append(f.format(v))
        return (SEP if self.separated else "").join(parts)

    def split(self, line: str) -> list[str]:
        """Line -> per-field texts (line is padded to the record length)."""
        line = line.ljust(self.length)
        out, pos = [], 0
        for f in self.fields:
            out.append(line[pos : pos + f.width])
            pos += f.width + (1 if self.separated else 0)
        return out

    def check(self, line: str, strict_numeric: bool) -> str | None:
        if self.separated:
            if len(line) != self.length:
                return f"line must be exactly {self.length} chars (got {len(line)})"
            pos = 0
            for f in self.fields[:-1]:
                pos += f.width
                if line[pos] != SEP:
                    return f"expected '{SEP}' at column {pos + 1} after {f.key}"
                pos += 1
        for f, text in zip(self.fields, self.split(line)):
            if reason := f.check(text, strict_numeric):
                return reason
        return None

    def describe(self) -> str:
        rows = []
        for f in self.fields:
            rows.append(f"  col {f.offset + 1:>3}-{f.offset + f.width:<3} {f.describe()}")
        return "\n".join(rows)


@dataclass
class Layout:
    """Complete stdin contract of an executable module."""

    module: str
    mode: str                                  # call | file
    records: list[Record] = field(default_factory=list)
    params: list[str] = field(default_factory=list)

    @property
    def max_length(self) -> int:
        return max((r.length for r in self.records), default=0)

    def record_for(self, line: str) -> Record | None:
        if self.mode == "call" or len(self.records) == 1:
            return self.records[0] if self.records else None
        for r in self.records:
            tf = r.type_field()
            if tf and line[:1] == tf.f.values_88[0]:
                return r
        return None

    def record_by_name(self, name: str | None) -> Record:
        if name is None:
            if len(self.records) == 1:
                return self.records[0]
            raise ValueError(f"'record' is required; one of {[r.name for r in self.records]}")
        for r in self.records:
            if r.name == name.upper():
                return r
        raise ValueError(f"unknown record {name!r}; one of {[r.name for r in self.records]}")


def _assign(fields: list[IOField], separated: bool) -> None:
    pos = 0
    for f in fields:
        f.offset = pos
        pos += f.width + (1 if separated else 0)


def call_layout(info: ModuleInfo) -> Layout:
    """Layout for a CALLed subprogram (driver-based)."""
    fields: list[IOField] = []
    params = info.linkage_params()
    for p in params:
        if p.occurs:
            raise UnsupportedCobol(f"{info.name}: OCCURS on parameter {p.name}")
        for f in flatten(p):
            fields.append(IOField(param=p.name, f=f, key=f.name))
    counts: dict[str, int] = {}
    for f in fields:
        counts[f.name] = counts.get(f.name, 0) + 1
    for f in fields:  # disambiguate duplicate leaf names
        if counts[f.name] > 1:
            f.key = ".".join([*f.f.path[1:], f.name]) if len(f.f.path) > 1 else f"{f.param}.{f.name}"
            if sum(1 for g in fields if g.key == f.key) > 1:
                f.key = f"{f.param}.{f.key}"
    if not fields:
        raise UnsupportedCobol(f"{info.name}: no elementary parameter fields")
    _assign(fields, True)
    return Layout(info.name, "call", [Record("CALL", fields, separated=True)], [p.name for p in params])


def file_layout(info: ModuleInfo) -> Layout:
    """Layout for a main program reading its input file from stdin."""
    records: list[Record] = []
    for rec in info.file_records:
        fields = [IOField(param=rec.name, f=f, key=f.name) for f in flatten(rec, allow_occurs=False)]
        _assign(fields, False)
        records.append(Record(rec.name, fields))
    return Layout(info.name, "file", records)


def layout_for(info: ModuleInfo) -> Layout:
    if info.kind == "subprogram":
        return call_layout(info)
    if info.kind == "program":
        return file_layout(info)
    raise UnsupportedCobol(f"{info.name}: {info.kind} is not executable")


def printable(text: str) -> bool:
    return all(c in PRINTABLE for c in text)
