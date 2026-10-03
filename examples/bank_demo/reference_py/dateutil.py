# module: DATEUTIL
"""DATEUTIL - calendar services. Day numbers count from 1900-01-01 (= day 1, a Monday)."""

from decimal import Decimal

import lbrt

CUM_DAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
MONTH_LEN = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

# WORKING-STORAGE (persists between calls)
ws = {"YYYY": 0, "MM": 0, "DD": 0, "LEAP": " ", "VALID": " ", "DAYNUM": 0, "DAYNUM-1": 0,
      "Y": 0, "Q": 0, "R": 0, "LEAPS": 0, "YEAR-LEN": 0, "MONTH-LEN": 0, "REMAIN": 0}


def _set_date_n(value) -> None:
    """MOVE to WS-DATE-N (9(8)), which redefines YYYY/MM/DD."""
    n = int(lbrt.store(value, 8, 0, signed=False))
    ws["YYYY"], ws["MM"], ws["DD"] = n // 10000, (n // 100) % 100, n % 100


def _date_n() -> int:
    return ws["YYYY"] * 10000 + ws["MM"] * 100 + ws["DD"]


def _leap_year() -> None:
    y = ws["Y"]
    ws["LEAP"] = "N"
    if y % 4 == 0:
        ws["LEAP"] = "Y"
        if y % 100 == 0:
            ws["LEAP"] = "N"
            if y % 400 == 0:
                ws["LEAP"] = "Y"


def _check_date() -> None:
    ws["VALID"] = "Y"
    if ws["YYYY"] < 1900 or ws["YYYY"] > 2099:
        ws["VALID"] = "N"
    if ws["MM"] < 1 or ws["MM"] > 12:
        ws["VALID"] = "N"
    if ws["VALID"] == "Y":
        ws["Y"] = ws["YYYY"]
        _leap_year()
        max_day = MONTH_LEN[ws["MM"] - 1]
        if ws["MM"] == 2 and ws["LEAP"] == "Y":
            max_day += 1
        if ws["DD"] < 1 or ws["DD"] > max_day:
            ws["VALID"] = "N"


def _date_to_daynum() -> None:
    y = ws["YYYY"] - 1
    ws["Y"] = y
    leaps = y // 4 - y // 100 + y // 400 - 460
    ws["LEAPS"] = leaps
    daynum = (ws["YYYY"] - 1900) * 365 + leaps + CUM_DAYS[ws["MM"] - 1] + ws["DD"]
    if ws["MM"] > 2 and ws["LEAP"] == "Y":
        daynum += 1
    ws["DAYNUM"] = int(lbrt.store(daynum, 7))


def _set_year_len() -> None:
    ws["YEAR-LEN"] = 366 if ws["LEAP"] == "Y" else 365


def _set_month_len() -> None:
    ws["MONTH-LEN"] = MONTH_LEN[ws["MM"] - 1] + (1 if ws["MM"] == 2 and ws["LEAP"] == "Y" else 0)


def _daynum_to_date() -> None:
    ws["REMAIN"] = ws["DAYNUM"]
    ws["Y"] = 1900
    _leap_year()
    _set_year_len()
    while not ws["REMAIN"] <= ws["YEAR-LEN"]:
        ws["REMAIN"] -= ws["YEAR-LEN"]
        ws["Y"] += 1
        _leap_year()
        _set_year_len()
    ws["YYYY"] = ws["Y"]
    ws["MM"] = 1
    _set_month_len()
    while not ws["REMAIN"] <= ws["MONTH-LEN"]:
        ws["REMAIN"] -= ws["MONTH-LEN"]
        ws["MM"] += 1
        _set_month_len()
    ws["DD"] = ws["REMAIN"]


def dateutil(p: dict) -> None:
    """CALL 'DATEUTIL' USING DATE-PARMS (mutates `p` in place)."""
    p["DP-RETURN-CODE"] = Decimal(0)
    fn = p["DP-FUNCTION"]
    if fn == "V":
        _set_date_n(p["DP-DATE-1"])
        _check_date()
        if ws["VALID"] == "N":
            p["DP-RETURN-CODE"] = Decimal(10)
    elif fn == "B":
        _set_date_n(p["DP-DATE-1"])
        _check_date()
        if ws["VALID"] == "Y":
            _date_to_daynum()
            ws["DAYNUM-1"] = ws["DAYNUM"]
            _set_date_n(p["DP-DATE-2"])
            _check_date()
        if ws["VALID"] == "Y":
            _date_to_daynum()
            p["DP-DAYS"] = lbrt.store(ws["DAYNUM"] - ws["DAYNUM-1"], 7)
        else:
            p["DP-RETURN-CODE"] = Decimal(10)
    elif fn == "A":
        _set_date_n(p["DP-DATE-1"])
        _check_date()
        if ws["VALID"] == "N":
            p["DP-RETURN-CODE"] = Decimal(10)
        else:
            _date_to_daynum()
            ws["DAYNUM"] = int(lbrt.store(ws["DAYNUM"] + p["DP-DAYS"], 7))   # ADD: silent truncation
            if ws["DAYNUM"] < 1 or ws["DAYNUM"] > 73049:
                p["DP-RETURN-CODE"] = Decimal(20)
            else:
                _daynum_to_date()
                p["DP-RESULT-DATE"] = Decimal(_date_n())
    elif fn == "W":
        _set_date_n(p["DP-DATE-1"])
        _check_date()
        if ws["VALID"] == "N":
            p["DP-RETURN-CODE"] = Decimal(10)
        else:
            _date_to_daynum()
            ws["DAYNUM"] = int(lbrt.store(ws["DAYNUM"] - 1, 7))
            q = lbrt.int_div(ws["DAYNUM"], 7)
            ws["Q"] = int(lbrt.store(q, 4, signed=False))
            ws["R"] = int(lbrt.store(ws["DAYNUM"] - q * 7, 4, signed=False))
            p["DP-DOW"] = lbrt.store(ws["R"] + 1, 1, signed=False)
    else:
        p["DP-RETURN-CODE"] = Decimal(99)
