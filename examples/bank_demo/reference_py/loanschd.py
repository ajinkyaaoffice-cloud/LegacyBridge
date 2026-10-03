# module: LOANSCHD
"""LOANSCHD - loan amortization schedule with refinance check and formatted header/footer."""

import sys
from decimal import Decimal

import amort
import fmtutil
import lbrt
import loanchk

REC_LEN = 38
D = Decimal

loan_parms = {"LP-PRINCIPAL": D(0), "LP-ANNUAL-RATE": D(0), "LP-TERM-MONTHS": D(0), "LP-PAYMENT": D(0),
              "LP-TOTAL-INTEREST": D(0), "LP-RETURN-CODE": D(0)}
acct_rec = {"ACCT-ID": " " * 8, "ACCT-NAME": " " * 20, "ACCT-TYPE": " ", "ACCT-BALANCE": D(0),
            "ACCT-RATE": D(0), "ACCT-OPEN-DATE": D(0), "ACCT-STATUS": " "}
lchk_parms = {"LC-FUNCTION": " ", "LC-RESULT": " ", "LC-REASON": " " * 20}
fmt_parms = {"FM-STYLE": " ", "FM-AMOUNT": D(0), "FM-DATE": D(0), "FM-RATE": D(0), "FM-OUT": " " * 20}
rec_no = D(0)
loans = D(0)


def _header(rec: str, principal: Decimal, rate: Decimal) -> None:
    acct_rec.update({"ACCT-ID": rec[1:9], "ACCT-NAME": " " * 20, "ACCT-TYPE": "L",
                     "ACCT-BALANCE": lbrt.store(0 - principal, 9, 2),
                     "ACCT-RATE": lbrt.store(rate, 1, 4, signed=False),
                     "ACCT-OPEN-DATE": lbrt.parse_num(rec[30:38]), "ACCT-STATUS": rec[29]})
    lchk_parms["LC-FUNCTION"] = "R"
    loanchk.loanchk(acct_rec, lchk_parms)
    fmt_parms["FM-STYLE"] = "B"
    fmt_parms["FM-AMOUNT"] = loan_parms["LP-PAYMENT"]
    fmtutil.fmtutil(fmt_parms)
    pmt = fmt_parms["FM-OUT"]
    fmt_parms["FM-STYLE"] = "P"
    fmt_parms["FM-RATE"] = rate
    fmtutil.fmtutil(fmt_parms)
    lbrt.display("LOAN ", rec[1:9], " PMT=", pmt, " RATE=", fmt_parms["FM-OUT"][:8],
                 " REFI=", lchk_parms["LC-RESULT"], " ", lchk_parms["LC-REASON"])


def _schedule(principal: Decimal, rate: Decimal, term: Decimal, show: Decimal) -> None:
    mrate = lbrt.store(rate / 12, 1, 9, signed=False)
    bal = lbrt.store(principal, 7, 2)
    last = show if show < term else term
    payment = loan_parms["LP-PAYMENT"]
    month = 1
    while not month > last:
        interest = lbrt.store(bal * mrate, 7, 2, rounded=True)
        if month == term:
            prin = bal
            pay = lbrt.store(prin + interest, 7, 2)
        else:
            prin = lbrt.store(payment - interest, 7, 2)
            pay = lbrt.store(payment, 7, 2)
        bal = lbrt.store(bal - prin, 7, 2)
        lbrt.display("  ", lbrt.edit(month, "ZZ9"), " ", lbrt.edit(pay, "ZZZ,ZZ9.99"), " ",
                     lbrt.edit(interest, "ZZZ,ZZ9.99"), " ", lbrt.edit(prin, "-ZZZ,ZZ9.99"), " ",
                     lbrt.edit(bal, "-Z,ZZZ,ZZ9.99"))
        month += 1


def _process(rec: str) -> None:
    global loans
    prin_x, rate_x, term_x, show_x, date_x = rec[9:18], rec[18:24], rec[24:27], rec[27:29], rec[30:38]
    if (rec[0] != "L" or not lbrt.is_numeric(prin_x) or not lbrt.is_numeric(rate_x) or not lbrt.is_numeric(term_x)
            or not lbrt.is_numeric(show_x) or not lbrt.is_numeric(date_x)):
        lbrt.display("BAD LOAN RECORD ", lbrt.fmt_num(rec_no, 5))
        return
    principal, rate = lbrt.parse_num(prin_x, 2), lbrt.parse_num(rate_x, 5)
    term, show = lbrt.parse_num(term_x), lbrt.parse_num(show_x)
    loan_parms["LP-PRINCIPAL"] = principal
    loan_parms["LP-ANNUAL-RATE"] = rate
    loan_parms["LP-TERM-MONTHS"] = term
    amort.amort(loan_parms)
    if loan_parms["LP-RETURN-CODE"] != 0:
        lbrt.display("LOAN ", rec[1:9], " ERROR RC=", lbrt.fmt_num(loan_parms["LP-RETURN-CODE"], 2))
        return
    loans = lbrt.store(loans + 1, 3, signed=False)
    _header(rec, principal, rate)
    _schedule(principal, rate, term, show)
    fmt_parms["FM-STYLE"] = "A"
    fmt_parms["FM-AMOUNT"] = loan_parms["LP-TOTAL-INTEREST"]
    fmtutil.fmtutil(fmt_parms)
    lbrt.display("  TOTAL INTEREST ", fmt_parms["FM-OUT"])


def main() -> int:
    """Run LOANSCHD over stdin."""
    global rec_no
    for rec in lbrt.read_records(sys.stdin, REC_LEN):
        rec_no = lbrt.store(rec_no + 1, 5, signed=False)
        _process(rec)
    lbrt.display("LOANS PROCESSED ", lbrt.display_num(loans, 3))
    return 0
