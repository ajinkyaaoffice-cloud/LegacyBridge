# module: LOANCHK
"""LOANCHK - loan limit checks (C) and refinance eligibility (R, calls ACCTVAL 'S')."""

from decimal import Decimal

import acctval
import lbrt

val_parms = {"VP-FUNCTION": " ", "VP-RESULT": "  ", "VP-MESSAGE": " " * 30}


def _no(lc: dict, reason: str) -> None:
    lc["LC-RESULT"] = "N"
    lc["LC-REASON"] = lbrt.alnum(reason, 20)


def loanchk(acct: dict, lc: dict) -> None:
    """CALL 'LOANCHK' USING ACCT-REC LCHK-PARMS."""
    lc["LC-RESULT"] = "Y"
    lc["LC-REASON"] = " " * 20
    fn = lc["LC-FUNCTION"]
    bal, rate = acct["ACCT-BALANCE"], acct["ACCT-RATE"]
    if fn == "C":
        if bal > 0:
            _no(lc, "CREDIT BALANCE")
        elif bal < Decimal("-500000.00"):
            _no(lc, "OVER LOAN LIMIT")
        if lc["LC-RESULT"] == "Y" and (rate < Decimal("0.0100") or rate > Decimal("0.2500")):
            _no(lc, "RATE OUT OF RANGE")
    elif fn == "R":
        val_parms["VP-FUNCTION"] = "S"
        acctval.acctval(acct, val_parms)
        if val_parms["VP-RESULT"] != "OK":
            _no(lc, val_parms["VP-MESSAGE"])
        elif bal > Decimal("-5000.00") or bal < Decimal("-250000.00"):
            _no(lc, "BALANCE NOT ELIGIBLE")
        elif not rate > Decimal("0.0600"):
            _no(lc, "RATE ALREADY LOW")
    else:
        _no(lc, "BAD FUNCTION")
