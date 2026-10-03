# module: STMTRPT
"""STMTRPT - customer statement report, 10 detail lines per page."""

import sys
from decimal import Decimal

import dateutil
import fmtutil
import lbrt

REC_LEN = 49
D = Decimal
DAY_NAMES = "MONTUEWEDTHUFRISATSUN"
COLUMNS = lbrt.alnum("DATE  DAY DESCRIPTION          AMOUNT            BALANCE", 60)

date_parms = {"DP-FUNCTION": " ", "DP-DATE-1": D(0), "DP-DATE-2": D(0), "DP-DAYS": D(0),
              "DP-RESULT-DATE": D(0), "DP-DOW": D(0), "DP-RETURN-CODE": D(0)}
fmt_parms = {"FM-STYLE": " ", "FM-AMOUNT": D(0), "FM-DATE": D(0), "FM-RATE": D(0), "FM-OUT": " " * 20}
in_stmt = "N"
bal = D(0)
page = D(0)
lines = D(0)
debits = D(0)
credits = D(0)
op_id = " " * 8
op_name = " " * 20


def _format(style: str, **values) -> str:
    fmt_parms["FM-STYLE"] = style
    fmt_parms.update(values)
    fmtutil.fmtutil(fmt_parms)
    return fmt_parms["FM-OUT"]


def _page_header() -> None:
    lbrt.display("STATEMENT FOR ", op_id, " ", op_name, " PAGE ", lbrt.edit(page, "ZZ9"))


def _open_statement(rec: str) -> None:
    global in_stmt, bal, page, lines, debits, credits, op_id, op_name
    in_stmt = "Y"
    bal_x = rec[37:49]
    bal = lbrt.parse_num(bal_x, 2, sign_separate=True) if lbrt.is_numeric(bal_x, sign_separate=True) else D(0)
    page = D(1)
    lines = debits = credits = D(0)
    op_id, op_name = rec[1:9], rec[9:29]
    _page_header()
    open_bal = _format("A", **{"FM-AMOUNT": bal})
    date_x = rec[29:37]
    stmt_date = _format("D", **{"FM-DATE": lbrt.parse_num(date_x) if lbrt.is_numeric(date_x) else D(0)})
    lbrt.display("STATEMENT DATE ", stmt_date)
    lbrt.display("OPENING BALANCE ", open_bal)
    lbrt.display(COLUMNS)


def _detail(rec: str) -> None:
    date_x, amt_x = rec[1:9], rec[10:19]
    if not lbrt.is_numeric(date_x) or not lbrt.is_numeric(amt_x):
        lbrt.display("INVALID DATE      ", date_x)
        return
    date_parms["DP-FUNCTION"] = "W"
    date_parms["DP-DATE-1"] = lbrt.parse_num(date_x)
    dateutil.dateutil(date_parms)
    if date_parms["DP-RETURN-CODE"] != 0:
        lbrt.display("INVALID DATE      ", date_x)
    else:
        _print_detail(rec, date_x, lbrt.parse_num(amt_x, 2))


def _print_detail(rec: str, date_x: str, amount: Decimal) -> None:
    global page, lines, debits, credits, bal
    if lines == 10:
        lbrt.display("--- CONTINUED ---")
        page = lbrt.store(page + 1, 3, signed=False)
        _page_header()
        lbrt.display(COLUMNS)
        lines = D(0)
    if rec[9] in ("W", "F"):
        signed_amt = lbrt.store(0 - amount, 9, 2)
        debits = lbrt.store(debits + 1, 4, signed=False)
    else:
        signed_amt = lbrt.store(amount, 9, 2)
        credits = lbrt.store(credits + 1, 4, signed=False)
    bal = lbrt.store(bal + signed_amt, 9, 2)
    dow = int(date_parms["DP-DOW"])
    day = DAY_NAMES[(dow - 1) * 3:dow * 3]
    amt_text = _format("B", **{"FM-AMOUNT": signed_amt})[:17]
    lbrt.display(date_x[4:6], "/", date_x[6:8], " ", day, " ", rec[19:39], " ", amt_text,
                 lbrt.edit(bal, "-ZZZ,ZZZ,ZZ9.99"))
    lines = lbrt.store(lines + 1, 2, signed=False)


def _close_statement() -> None:
    global in_stmt
    close_bal = _format("R", **{"FM-AMOUNT": bal})
    lbrt.display("CLOSING BALANCE ", close_bal, " DEBITS=", lbrt.edit(debits, "ZZZ9"),
                 " CREDITS=", lbrt.edit(credits, "ZZZ9"))
    in_stmt = "N"


def main() -> int:
    """Run STMTRPT over stdin."""
    for rec in lbrt.read_records(sys.stdin, REC_LEN):
        if rec[0] == "H":
            if in_stmt == "Y":
                _close_statement()
            _open_statement(rec)
        elif rec[0] == "T":
            if in_stmt == "N":
                lbrt.display("NO HEADER - TRANSACTION SKIPPED")
            else:
                _detail(rec)
        else:
            lbrt.display("UNKNOWN RECORD SKIPPED")
    if in_stmt == "Y":
        _close_statement()
    return 0
