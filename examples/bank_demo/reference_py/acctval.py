# module: ACCTVAL
"""ACCTVAL - account validation. V = full validation, S = status only."""

from decimal import Decimal

import dateutil
import lbrt
import loanchk

date_parms = {"DP-FUNCTION": " ", "DP-DATE-1": Decimal(0), "DP-DATE-2": Decimal(0), "DP-DAYS": Decimal(0),
              "DP-RESULT-DATE": Decimal(0), "DP-DOW": Decimal(0), "DP-RETURN-CODE": Decimal(0)}
lchk_parms = {"LC-FUNCTION": " ", "LC-RESULT": " ", "LC-REASON": " " * 20}


def _set(v: dict, code: str, message: str) -> None:
    v["VP-RESULT"] = code
    v["VP-MESSAGE"] = lbrt.alnum(message, 30)


def _check_status(acct: dict, v: dict) -> None:
    status = acct["ACCT-STATUS"]
    if status == "A":
        pass
    elif status == "F":
        _set(v, "E4", "ACCOUNT FROZEN")
    elif status == "C":
        _set(v, "E5", "ACCOUNT CLOSED")
    else:
        _set(v, "E8", "UNKNOWN STATUS CODE")


def _full_validation(acct: dict, v: dict) -> None:
    if acct["ACCT-ID"].strip() == "":
        _set(v, "E1", "ACCOUNT ID MISSING")
    elif acct["ACCT-TYPE"] not in ("C", "S", "L"):
        _set(v, "E2", "INVALID ACCOUNT TYPE")
    if v["VP-RESULT"] == "OK":
        date_parms["DP-FUNCTION"] = "V"
        date_parms["DP-DATE-1"] = acct["ACCT-OPEN-DATE"]
        dateutil.dateutil(date_parms)
        if date_parms["DP-RETURN-CODE"] != 0:
            _set(v, "E3", "INVALID OPEN DATE")
    if v["VP-RESULT"] == "OK":
        _check_status(acct, v)
    if v["VP-RESULT"] == "OK" and acct["ACCT-TYPE"] == "S" and acct["ACCT-BALANCE"] < 0:
        _set(v, "E7", "SAVINGS BALANCE NEGATIVE")
    if v["VP-RESULT"] == "OK" and acct["ACCT-TYPE"] == "L":
        lchk_parms["LC-FUNCTION"] = "C"
        loanchk.loanchk(acct, lchk_parms)
        if lchk_parms["LC-RESULT"] != "Y":
            v["VP-RESULT"] = "E6"
            s = "LOAN: " + lchk_parms["LC-REASON"]          # STRING ... DELIMITED BY SIZE
            v["VP-MESSAGE"] = (s + v["VP-MESSAGE"][len(s):])[:30]


def acctval(acct: dict, v: dict) -> None:
    """CALL 'ACCTVAL' USING ACCT-REC VAL-PARMS."""
    v["VP-RESULT"] = "OK"
    v["VP-MESSAGE"] = " " * 30
    fn = v["VP-FUNCTION"]
    if fn == "S":
        _check_status(acct, v)
    elif fn == "V":
        _full_validation(acct, v)
    else:
        _set(v, "E9", "INVALID FUNCTION CODE")
