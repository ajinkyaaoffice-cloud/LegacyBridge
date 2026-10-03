# module: FMTUTIL
"""FMTUTIL - report field formatting. Style D does NOT clear FM-OUT first (historical)."""

import lbrt


def fmtutil(p: dict) -> None:
    """CALL 'FMTUTIL' USING FMT-PARMS."""
    style = p["FM-STYLE"]
    if style == "A":
        p["FM-OUT"] = lbrt.alnum(lbrt.edit(p["FM-AMOUNT"], "ZZZ,ZZZ,ZZ9.99CR"), 20)
    elif style == "B":
        p["FM-OUT"] = lbrt.alnum(lbrt.edit(p["FM-AMOUNT"], "--,---,---,--9.99"), 20)
    elif style == "R":
        right = lbrt.alnum(lbrt.edit(p["FM-AMOUNT"], "ZZZ,ZZZ,ZZ9.99CR"), 20, justified_right=True)
        p["FM-OUT"] = right
    elif style == "D":
        d = lbrt.fmt_num(p["FM-DATE"], 8)            # MOVE 9(8) TO X(8): the digits
        s = d[4:6] + "/" + d[6:8] + "/" + d[0:4]     # STRING ... DELIMITED BY SIZE
        out = lbrt.alnum(p["FM-OUT"], 20)
        p["FM-OUT"] = (s + out[len(s):])[:20]        # only the written positions change
    elif style == "P":
        pct = lbrt.store(p["FM-RATE"] * 100, 3, 3, signed=False)
        p["FM-OUT"] = lbrt.alnum(lbrt.edit(pct, "ZZ9.999") + "%", 20)
    else:
        p["FM-OUT"] = "*" * 20
