# module: FEECALC
"""FEECALC - monthly account fees."""

from decimal import Decimal

import lbrt

monthly_fee = Decimal("0.00")   # WS-MONTHLY-FEE S9(5)V99
txn_fee = Decimal("0.00")       # WS-TXN-FEE     S9(5)V99
od_fee = Decimal("0.00")        # WS-OD-FEE      S9(5)V99
excess = Decimal(0)             # WS-EXCESS      S9(3)


def feecalc(p: dict) -> None:
    """CALL 'FEECALC' USING FEE-PARMS."""
    global monthly_fee, txn_fee, od_fee, excess
    monthly_fee = txn_fee = od_fee = Decimal("0.00")
    t = p["FP-ACCT-TYPE"]
    bal = p["FP-BALANCE"]
    if t == "C":
        if bal < Decimal("1500.00"):
            monthly_fee = Decimal("12.00")
        excess = lbrt.store(p["FP-TXN-COUNT"] - 20, 3)
        if excess > 0:
            txn_fee = lbrt.store(excess * Decimal("0.35"), 5, 2)
        if bal < 0:
            od_fee = lbrt.store(Decimal("35.00") + (0 - bal) * Decimal("0.015"), 5, 2, rounded=True)
    elif t == "S":
        if bal < Decimal("300.00"):
            monthly_fee = Decimal("5.00")
        excess = lbrt.store(p["FP-TXN-COUNT"] - 6, 3)
        if excess > 0:
            txn_fee = lbrt.store(excess * Decimal("2.50"), 5, 2)
    elif t == "L":
        pass
    else:
        p["FP-FEE"] = Decimal("0.00")
        p["FP-WAIVED"] = "X"
        return
    p["FP-FEE"] = lbrt.store(monthly_fee + txn_fee + od_fee, 5, 2)
    if p["FP-FEE"] > Decimal("250.00"):
        p["FP-FEE"] = Decimal("250.00")
    p["FP-WAIVED"] = "Y" if p["FP-FEE"] == 0 else "N"
