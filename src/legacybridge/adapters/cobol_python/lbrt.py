"""lbrt - LegacyBridge COBOL runtime helpers for translated Python code.

Shipped verbatim next to every translated module (and in the final package). Pure stdlib.
Semantics follow GnuCOBOL 3.x defaults, verified empirically by tests/test_lbrt.py.

Numbers are decimal.Decimal everywhere. Key rules:
  * Storing into PIC [S]9(i)V9(s): decimals are TRUNCATED (or rounded half away from zero when
    ROUNDED), high-order digits beyond i are silently DROPPED, unsigned fields drop the sign.
  * ON SIZE ERROR: if the (rounded/truncated) value doesn't fit, the target is left UNCHANGED.
  * Intermediate arithmetic is exact (use these helpers only when storing).
"""

from __future__ import annotations

import sys
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, getcontext
from typing import Iterable, Iterator, TextIO

getcontext().prec = 80

__all__ = [
    "D", "store", "fits", "size_error", "edit", "alnum", "display_num", "display", "parse_num", "is_numeric",
    "fmt_num", "read_records", "int_div", "remainder", "ZERO",
]

ZERO = Decimal(0)


def D(x: object) -> Decimal:
    """Coerce int/str/Decimal to Decimal (never pass floats)."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        raise TypeError("lbrt: floats are not allowed for COBOL arithmetic; use Decimal")
    return Decimal(x)


def _quantize(value: Decimal, scale: int, rounded: bool) -> Decimal:
    q = Decimal(1).scaleb(-scale)
    return value.quantize(q, rounding=ROUND_HALF_UP if rounded else ROUND_DOWN)


def store(value: object, digits: int, scale: int = 0, signed: bool = True, rounded: bool = False) -> Decimal:
    """Value as stored by MOVE/COMPUTE into PIC [S]9(digits)V9(scale) without SIZE ERROR."""
    v = _quantize(D(value), scale, rounded)
    limit = Decimal(10) ** digits
    sign = -1 if v < 0 else 1
    mag = abs(v) % limit                 # drop high-order integer digits
    v = mag if (sign > 0 or not signed) else -mag
    return v + ZERO if v != 0 else Decimal(0).quantize(Decimal(1).scaleb(-scale))


def fits(value: object, digits: int, scale: int = 0, rounded: bool = False) -> bool:
    """True if `value` (after rounding/truncating decimals) fits in `digits` integer digits."""
    v = _quantize(D(value), scale, rounded)
    return abs(v) < Decimal(10) ** digits


def size_error(value: object, digits: int, scale: int = 0, rounded: bool = False) -> bool:
    """True when an arithmetic statement with ON SIZE ERROR would take the error branch."""
    return not fits(value, digits, scale, rounded)


def int_div(a: object, b: object) -> Decimal:
    """Quotient for DIVIDE ... GIVING an integer field (truncated toward zero)."""
    return (D(a) / D(b)).to_integral_value(rounding=ROUND_DOWN)


def remainder(a: object, b: object) -> Decimal:
    """REMAINDER of DIVIDE a BY b GIVING integer q: a - q*b."""
    return D(a) - int_div(a, b) * D(b)


def alnum(value: object, length: int, justified_right: bool = False) -> str:
    """MOVE to PIC X(length): pad with spaces / truncate (right side, or left when JUSTIFIED)."""
    s = str(value)
    if justified_right:
        return s[-length:].rjust(length) if len(s) >= length else s.rjust(length)
    return s[:length].ljust(length)


def fmt_num(value: object, digits: int, scale: int = 0, signed: bool = False,
            sign_separate: bool = False) -> str:
    """Raw characters of a DISPLAY numeric field in a record: zero-padded digits (implied
    decimal point, no '.'), with a leading '+'/'-' when SIGN LEADING SEPARATE."""
    v = store(value, digits, scale, signed)
    s = str(int(abs(v).scaleb(scale))).rjust(digits + scale, "0")
    if sign_separate:
        return ("-" if v < 0 else "+") + s
    return s


def display_num(value: object, digits: int, scale: int = 0, signed: bool = False) -> str:
    """Text printed by `DISPLAY numeric-item` (GnuCOBOL): sign if signed, '.' if scaled.
    e.g. S9(7)V99 165.28 -> '+0000165.28', 9(5) 3 -> '00003', S9(5) -3 -> '-00003'."""
    v = store(value, digits, scale, signed)
    q = str(int(abs(v).scaleb(scale))).rjust(digits + scale, "0")
    body = q[:digits] + ("." + q[digits:] if scale else "")
    if signed:
        return ("-" if v < 0 else "+") + body
    return body


def display(*parts: object, file: TextIO | None = None) -> None:
    """DISPLAY a b c: concatenates parts with no separator and writes a newline."""
    (file or sys.stdout).write("".join(str(p) for p in parts) + "\n")


def is_numeric(text: str, signed: bool = False, sign_separate: bool = False) -> bool:
    """`IS NUMERIC` on a DISPLAY field. SIGN LEADING SEPARATE fields need an explicit +/-."""
    if sign_separate:
        return len(text) >= 2 and text[0] in "+-" and text[1:].isdigit() and text[1:].isascii()
    return text.isdigit() and text.isascii()


def parse_num(text: str, scale: int = 0, sign_separate: bool = False) -> Decimal:
    """Value of a DISPLAY numeric field (assumes is_numeric() is True)."""
    sign = 1
    if sign_separate:
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    return (Decimal(int(text)) * sign).scaleb(-scale)


def read_records(stream: TextIO, length: int) -> Iterator[str]:
    """Records of a LINE SEQUENTIAL file: newline/CR stripped, padded with spaces to `length`
    (inputs are guaranteed not to exceed the record length)."""
    for line in stream:
        line = line.rstrip("\n").rstrip("\r")
        yield line[:length].ljust(length)


# ---------------------------------------------------------------- numeric editing


def _expand(pic: str) -> str:
    out, i = [], 0
    while i < len(pic):
        ch = pic[i]
        if i + 1 < len(pic) and pic[i + 1] == "(":
            j = pic.index(")", i)
            out.append(ch * int(pic[i + 2 : j]))
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out).upper()


def edit(value: object, picture: str) -> str:
    """MOVE a number to a numeric-edited PICTURE (Z 9 * , . B 0 / + - $ CR DB, floating + - $).

    High-order digits that don't fit are dropped, decimals truncated (standard MOVE)."""
    pic = _expand(picture)
    v = D(value)
    negative = v < 0
    trailer = ""
    if pic.endswith("CR") or pic.endswith("DB"):
        trailer, pic = pic[-2:], pic[:-2]
    # fixed sign at either end (not part of a floating string)
    lead_sign = trail_sign = ""
    if pic and pic[-1] in "+-" and not (len(pic) > 1 and pic[-2] == pic[-1]):
        trail_sign, pic = pic[-1], pic[:-1]
    float_ch = ""
    for ch in "+-$":
        if pic.count(ch) >= 2:
            float_ch = ch
            break
    if not float_ch and pic and pic[0] in "+-":
        lead_sign, pic = pic[0], pic[1:]
    fixed_currency = ""
    if not float_ch and pic.startswith("$"):
        fixed_currency, pic = "$", pic[1:]
    if float_ch == "$" and pic and pic[0] in "+-" and pic.count(pic[0]) == 1:
        lead_sign, pic = pic[0], pic[1:]

    # digit positions
    first_float = pic.find(float_ch) if float_ch else -1
    kinds: list[str] = []  # per char: 'd' digit, 'i' insertion, '.', 's' float-symbol-only
    for idx, ch in enumerate(pic):
        if ch in "9Z*":
            kinds.append("d")
        elif float_ch and ch == float_ch:
            kinds.append("s" if idx == first_float else "d")
        elif ch == ".":
            kinds.append(".")
        else:  # , B 0 /
            kinds.append("i")
    dot = pic.find(".")
    n_int = sum(1 for k in kinds[: dot if dot >= 0 else len(kinds)] if k == "d")
    n_frac = sum(1 for k in kinds[dot + 1 :] if k == "d") if dot >= 0 else 0
    mag = abs(v).quantize(Decimal(1).scaleb(-n_frac), rounding=ROUND_DOWN)
    mag = mag % (Decimal(10) ** n_int) if n_int else mag - int(mag)
    digits = str(int(mag.scaleb(n_frac))).rjust(n_int + n_frac, "0")
    if int(digits or "0") == 0:
        negative = False  # -0 prints as positive

    out: list[str] = []
    di = 0
    # GnuCOBOL quirk: with a fixed leading sign, a nonzero digit just above the kept window
    # (high-order truncation) disables zero suppression: 1000 -> '+ZZ9' gives '+000'.
    significant = bool(lead_sign) and int(abs(v) // (Decimal(10) ** n_int)) % 10 != 0
    check_protect = "*" in pic
    float_pos = -1
    for idx, (ch, kind) in enumerate(zip(pic, kinds)):
        if kind == "s":
            out.append(" ")
            float_pos = idx
            continue
        if kind == ".":
            significant = True
            out.append(".")
            continue
        if kind == "i":
            if significant:
                out.append({"B": " "}.get(ch, ch))
            else:
                out.append("*" if check_protect else " ")
                if float_ch:
                    float_pos = idx
            continue
        d = digits[di]
        di += 1
        if not significant and (d != "0" or ch == "9" or (dot >= 0 and idx > dot)):
            significant = True
        if significant:
            out.append(d)
        else:
            if ch == "*":
                out.append("*")
            else:
                out.append(" ")
                if float_ch:
                    float_pos = idx
    if float_ch:
        sym = {"$": "$", "+": "-" if negative else "+", "-": "-" if negative else " "}[float_ch]
        if float_pos >= 0:
            out[float_pos] = sym
    # all-Z zero value -> spaces
    if not significant and not check_protect and all(c in " ,./B0" for c in out):
        out = [" "] * len(out)
    s = "".join(out)
    if lead_sign:
        s = ({"+": "-" if negative else "+", "-": "-" if negative else " "}[lead_sign]) + s
    if fixed_currency:
        s = "$" + s
    if trail_sign:
        s += {"+": "-" if negative else "+", "-": "-" if negative else " "}[trail_sign]
    if trailer:
        s += trailer if negative else "  "
    return s
