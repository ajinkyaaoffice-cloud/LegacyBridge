# module: INTCALC
"""INTCALC - interest for a period: S = actual/365 (rounded, size-checked), M = 30/360
(truncated), C = daily compounding (max 3660 days)."""

from decimal import Decimal

import dateutil
import lbrt

# WORKING-STORAGE: COPY DATEPARM + work fields (persist between calls)
date_parms = {"DP-FUNCTION": " ", "DP-DATE-1": Decimal(0), "DP-DATE-2": Decimal(0), "DP-DAYS": Decimal(0),
              "DP-RESULT-DATE": Decimal(0), "DP-DOW": Decimal(0), "DP-RETURN-CODE": Decimal(0)}
days360 = Decimal(0)     # WS-DAYS360 S9(7)
cbal = Decimal(0)        # WS-CBAL    S9(11)V9(6)


def _actual_days(p: dict) -> None:
    date_parms["DP-FUNCTION"] = "B"
    date_parms["DP-DATE-1"] = p["IP-START-DATE"]
    date_parms["DP-DATE-2"] = p["IP-END-DATE"]
    dateutil.dateutil(date_parms)
    p["IP-DAYS"] = lbrt.store(date_parms["DP-DAYS"], 5)
    if p["IP-DAYS"] < 0:
        p["IP-RETURN-CODE"] = Decimal(20)


def _days_360(p: dict) -> None:
    global days360
    d1, d2 = int(p["IP-START-DATE"]), int(p["IP-END-DATE"])
    y1, m1, day1 = d1 // 10000, d1 // 100 % 100, d1 % 100
    y2, m2, day2 = d2 // 10000, d2 // 100 % 100, d2 % 100
    if day1 == 31:
        day1 = 30
    if day2 == 31 and day1 == 30:
        day2 = 30
    days360 = lbrt.store((y2 - y1) * 360 + (m2 - m1) * 30 + (day2 - day1), 7)
    if days360 < 0:
        p["IP-RETURN-CODE"] = Decimal(20)
    else:
        p["IP-DAYS"] = lbrt.store(days360, 5)


def _compound_daily(p: dict) -> None:
    global cbal
    cbal = lbrt.store(p["IP-PRINCIPAL"], 11, 6)
    i = 1
    while not i > p["IP-DAYS"]:
        cbal = lbrt.store(cbal + cbal * p["IP-ANNUAL-RATE"] / 365, 11, 6)
        i += 1
    p["IP-INTEREST"] = lbrt.store(cbal - p["IP-PRINCIPAL"], 7, 2, rounded=True)


def intcalc(p: dict) -> None:
    """CALL 'INTCALC' USING INT-PARMS."""
    p["IP-RETURN-CODE"] = Decimal(0)
    p["IP-INTEREST"] = Decimal("0.00")
    date_parms["DP-FUNCTION"] = "V"
    date_parms["DP-DATE-1"] = p["IP-START-DATE"]
    dateutil.dateutil(date_parms)
    if date_parms["DP-RETURN-CODE"] != 0:
        p["IP-RETURN-CODE"] = Decimal(10)
        return
    date_parms["DP-DATE-1"] = p["IP-END-DATE"]
    dateutil.dateutil(date_parms)
    if date_parms["DP-RETURN-CODE"] != 0:
        p["IP-RETURN-CODE"] = Decimal(10)
        return
    method = p["IP-METHOD"]
    if method == "S":
        _actual_days(p)
        if p["IP-RETURN-CODE"] == 0:
            v = p["IP-PRINCIPAL"] * p["IP-ANNUAL-RATE"] * p["IP-DAYS"] / 365
            if lbrt.size_error(v, 7, 2, rounded=True):
                p["IP-RETURN-CODE"] = Decimal(40)
                p["IP-INTEREST"] = Decimal("0.00")
            else:
                p["IP-INTEREST"] = lbrt.store(v, 7, 2, rounded=True)
    elif method == "M":
        _days_360(p)
        if p["IP-RETURN-CODE"] == 0:
            p["IP-INTEREST"] = lbrt.store(p["IP-PRINCIPAL"] * p["IP-ANNUAL-RATE"] * days360 / 360, 7, 2)
    elif method == "C":
        _actual_days(p)
        if p["IP-RETURN-CODE"] == 0 and p["IP-DAYS"] > 3660:
            p["IP-RETURN-CODE"] = Decimal(30)
        if p["IP-RETURN-CODE"] == 0:
            _compound_daily(p)
    else:
        p["IP-RETURN-CODE"] = Decimal(99)
