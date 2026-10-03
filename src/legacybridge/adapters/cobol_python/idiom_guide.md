# COBOL -> Python idiom guide (LegacyBridge)

Your translation is accepted only if it reproduces the ORIGINAL program's output byte for byte
(GnuCOBOL), on hundreds of generated inputs, including overflow, rounding and invalid data.
Translate the semantics COBOL actually has, not what the business logic "obviously means".

## Runtime: `import lbrt` (always available)

| COBOL                                   | Python                                                       |
|-----------------------------------------|--------------------------------------------------------------|
| store into `PIC S9(d)V9(s)`             | `x = lbrt.store(value, d, s)`  (truncates decimals, drops high-order digits) |
| store into unsigned `PIC 9(d)V9(s)`     | `x = lbrt.store(value, d, s, signed=False)` (sign is dropped) |
| `ROUNDED`                               | `lbrt.store(value, d, s, rounded=True)` (half away from zero) |
| `ON SIZE ERROR`                         | `if lbrt.size_error(value, d, s[, rounded=True]): <error branch; target UNCHANGED>` else store |
| `DIVIDE a BY b GIVING q REMAINDER r`    | `q = lbrt.store(lbrt.int_div(a, b), d)`; `r = lbrt.store(lbrt.remainder(a, b), d2)` |
| MOVE to `PIC X(n)`                      | `lbrt.alnum(value, n)` (pad/truncate right)                    |
| `JUSTIFIED RIGHT`                       | `lbrt.alnum(value, n, justified_right=True)`                  |
| MOVE number to edited PIC (`ZZ9.99CR`)  | `lbrt.edit(value, "ZZ9.99CR")`                                |
| numeric field inside a record / MOVE 9(n) to X(n) | `lbrt.fmt_num(value, d, s[, signed, sign_separate])` (zero-padded digits) |
| `DISPLAY numeric-item`                  | `lbrt.display_num(value, d, s, signed)` (e.g. `+0000165.28`)  |
| `DISPLAY a b c`                         | `lbrt.display(a, b, c)` (no separators, one newline)          |
| `x IS NUMERIC`                          | `lbrt.is_numeric(text[, sign_separate=True])` on the RAW field text |
| value of a DISPLAY numeric field        | `lbrt.parse_num(text, scale[, sign_separate=True])`          |
| read a LINE SEQUENTIAL file             | `for rec in lbrt.read_records(sys.stdin, MAX_RECORD_LEN):`   |

## Data model

- Numbers are `decimal.Decimal`. Never use float, never `round()`, never `int()` on a value
  that may have decimals (except to truncate deliberately toward zero).
- EVERY store into a numeric item goes through `lbrt.store` with that item's PIC, including
  intermediate WORKING-STORAGE items: COBOL truncates at each store, and later steps depend on the
  truncated value. COMPUTE evaluates the whole expression exactly, then stores once.
- `ADD a TO b` is `b = lbrt.store(b + a, d, s)`; `SUBTRACT a FROM b` likewise.
  `MULTIPLY a BY b` stores into b. `DIVIDE a INTO b` stores b / a into b.
- Division by zero is a SIZE ERROR: check `if divisor == 0` first; with ON SIZE ERROR take the
  error branch, without it leave the target unchanged.
- Unsigned fields store the absolute value (`signed=False`).
- Alphanumeric values are `str` of the exact field length (pad with `lbrt.alnum`). Comparisons of
  alphanumerics pad the shorter operand with spaces: `name == "ABC"` must compare against
  `lbrt.alnum("ABC", n)` or use `.rstrip()` on both sides.
- 88-levels are conditions on the parent field: `88 IS-ACTIVE VALUE 'A' 'B'` -> `status in ("A", "B")`.
- `MOVE SPACES`/`ZEROS`/`INITIALIZE`: alphanumerics become spaces, numerics `Decimal(0)`.
- Uninitialized WORKING-STORAGE (no VALUE): numerics start at 0, alphanumerics at spaces.

## Program structure

- WORKING-STORAGE = module-level variables, initialized once at import; persists across CALLs
  (and across records). Assign them inside functions with `global`. Do NOT reset them per call
  unless the COBOL code does.
- Paragraphs -> private functions; `PERFORM X` -> `_x()`; `PERFORM X THRU Y` -> call each in order.
- `PERFORM VARYING I FROM a BY b UNTIL cond` tests the condition BEFORE each iteration:
  `i = a` / `while not cond: body; i = lbrt.store(i + b, d)` (I keeps its PIC: it can wrap).
- `EVALUATE TRUE / WHEN c1 ... WHEN OTHER` -> `if/elif/else` in order (first match wins).
- `GOBACK`/`EXIT PROGRAM` -> `return`. `STOP RUN` in a main -> return the RETURN-CODE.
- Tables (`OCCURS n`) -> Python lists; COBOL subscripts are 1-based.
- CALL of another program: `import <target>` then `<target>.<target>(dict1, dict2)`, passing the
  SAME dict objects every time (BY REFERENCE: the callee updates them in place). Keep the dicts
  for the caller's parameter areas as module-level state, like COBOL.
- Subprogram entry: update the parameter dicts in place (never rebind them). Return None, or the
  RETURN-CODE int if the program sets RETURN-CODE.
- Main program: `def main() -> int:` reads `sys.stdin`, prints, returns RETURN-CODE. No code runs
  at import time except constant/state definitions.

## Records and output

- An FD record area is the raw line: slice fields by position from the padded record text,
  e.g. `acct_id = rec[1:9]` for columns 2-9. Several 01 records under one FD share the same text.
- Test numeric validity on the raw text (`lbrt.is_numeric(rec[30:42], sign_separate=True)`)
  BEFORE converting.
- DISPLAY of a group item prints ALL of its bytes, including trailing spaces of the last field and
  FILLER literals: build the line from every field at its full width.
- `STRING a b DELIMITED BY SIZE INTO t` overwrites only the characters it writes; the rest of `t`
  keeps its previous content (unless the code moved SPACES to it first):
  `s = a + b; t = (s + t[len(s):])[:len(t)]`.
  `DELIMITED BY SPACE` stops each source at its first space.
- Reference modification `X(start:len)` -> `x[start-1:start-1+len]`.

## Allowed imports

`decimal`, `math`, `sys`, `lbrt`, other translated modules, and similar pure-stdlib helpers listed in
the contract. No file/network/process access; no `exec`/`eval`/`open`.

## Example

```cobol
       01  WS-TOTAL     PIC S9(5)V99 VALUE 0.
       01  WS-CNT       PIC 9(3) VALUE 0.
       ...
           ADD LK-AMT TO WS-TOTAL
               ON SIZE ERROR MOVE 'Y' TO LK-OVFL
           END-ADD
           ADD 1 TO WS-CNT
           COMPUTE LK-AVG ROUNDED = WS-TOTAL / WS-CNT
```

```python
# module: EXAMPLE
from decimal import Decimal

import lbrt

total = Decimal(0)   # WS-TOTAL S9(5)V99
cnt = Decimal(0)     # WS-CNT   9(3)


def example(lk: dict) -> None:
    global total, cnt
    if lbrt.size_error(total + lk["LK-AMT"], 5, 2):
        lk["LK-OVFL"] = "Y"
    else:
        total = total + lk["LK-AMT"]
    cnt = lbrt.store(cnt + 1, 3, signed=False)
    lk["LK-AVG"] = lbrt.store(total / cnt, 5, 2, rounded=True)
```
