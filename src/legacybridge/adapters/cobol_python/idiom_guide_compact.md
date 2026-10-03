# COBOL -> Python rules (byte-exact output required)

- `import lbrt`. Numbers are Decimal (never float/round()).
- Every numeric store: `lbrt.store(v, int_digits, scale, signed=True|False, rounded=False)` (truncates, drops high digits).
- ON SIZE ERROR: `if lbrt.size_error(v, d, s[, rounded=True]):` error branch, target unchanged; else store. Divide by 0 = size error.
- DIVIDE GIVING/REMAINDER: `lbrt.int_div(a, b)`, `lbrt.remainder(a, b)`.
- PIC X(n): `lbrt.alnum(v, n[, justified_right=True])`. Edited PIC: `lbrt.edit(v, "ZZ9.99CR")`.
- Numeric field text in records: `lbrt.fmt_num(v, d, s)`. `DISPLAY num`: `lbrt.display_num(v, d, s, signed)`. `DISPLAY a b`: `lbrt.display(a, b)`.
- Input: `for rec in lbrt.read_records(sys.stdin, MAXLEN)`, slice fields by column; check `lbrt.is_numeric(raw[, sign_separate=True])` then `lbrt.parse_num(raw, scale[, sign_separate=True])`.
- WORKING-STORAGE = module globals initialized once (persist across calls). Params = dicts updated in place.
- CALL: `import x; x.x(d1, d2)` with persistent dicts. Main: `def main() -> int` returns RETURN-CODE; nothing runs at import.
- PERFORM VARYING tests the UNTIL condition before each iteration. 88-level = membership test on the parent.
- DISPLAY of a group prints every byte (trailing spaces included). STRING overwrites only written positions.
