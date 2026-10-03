# module: INTPOST
"""INTPOST - interest posting run: INTCALC per 'P' record, then a summary."""

import sys
from decimal import Decimal

import intcalc
import lbrt

REC_LEN = 44
D = Decimal

int_parms = {"IP-PRINCIPAL": D(0), "IP-ANNUAL-RATE": D(0), "IP-START-DATE": D(0), "IP-END-DATE": D(0),
             "IP-METHOD": " ", "IP-INTEREST": D(0), "IP-DAYS": D(0), "IP-RETURN-CODE": D(0)}
rec_no = D(0)
ok_count = D(0)
err_count = D(0)
total = D(0)


def _process(rec: str) -> None:
    global ok_count, err_count, total
    prin_x, rate_x, start_x, end_x = rec[9:21], rec[21:27], rec[27:35], rec[35:43]
    if (rec[0] != "P" or not lbrt.is_numeric(prin_x, sign_separate=True) or not lbrt.is_numeric(rate_x)
            or not lbrt.is_numeric(start_x) or not lbrt.is_numeric(end_x)):
        lbrt.display("BAD RECORD ", lbrt.edit(rec_no, "ZZZZ9"))
        err_count = lbrt.store(err_count + 1, 5, signed=False)
        return
    int_parms["IP-PRINCIPAL"] = lbrt.parse_num(prin_x, 2, sign_separate=True)
    int_parms["IP-ANNUAL-RATE"] = lbrt.parse_num(rate_x, 5)
    int_parms["IP-START-DATE"] = lbrt.parse_num(start_x)
    int_parms["IP-END-DATE"] = lbrt.parse_num(end_x)
    int_parms["IP-METHOD"] = rec[43]
    int_parms["IP-DAYS"] = D(0)
    intcalc.intcalc(int_parms)
    rc = int_parms["IP-RETURN-CODE"]
    lbrt.display(rec[1:9], " DAYS=", lbrt.edit(int_parms["IP-DAYS"], "-ZZZZ9"),
                 " INT=", lbrt.edit(int_parms["IP-INTEREST"], "-Z,ZZZ,ZZ9.99"), " RC=", lbrt.fmt_num(rc, 2))
    if rc == 0:
        ok_count = lbrt.store(ok_count + 1, 5, signed=False)
        total = lbrt.store(total + int_parms["IP-INTEREST"], 9, 2)
    else:
        err_count = lbrt.store(err_count + 1, 5, signed=False)


def main() -> int:
    """Run INTPOST over stdin."""
    global rec_no
    for rec in lbrt.read_records(sys.stdin, REC_LEN):
        rec_no = lbrt.store(rec_no + 1, 5, signed=False)
        _process(rec)
    lbrt.display("POSTED=", lbrt.edit(ok_count, "ZZZZ9"), " ERRORS=", lbrt.edit(err_count, "ZZZZ9"),
                 " TOTAL=", lbrt.edit(total, "-ZZZ,ZZZ,ZZ9.99"))
    if ok_count == 0:
        lbrt.display("NO INTEREST POSTED")
    else:
        avg = lbrt.store(total / ok_count, 7, 2, rounded=True)
        lbrt.display("AVERAGE INTEREST ", lbrt.display_num(avg, 7, 2, signed=True))
        lbrt.display("RECORDS READ ", lbrt.display_num(rec_no, 5))
    return 0
