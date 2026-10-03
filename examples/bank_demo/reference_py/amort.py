# module: AMORT
"""AMORT - level monthly payment for an amortizing loan."""

from decimal import Decimal

import lbrt

mrate = Decimal(0)     # WS-MRATE  9V9(9)
factor = Decimal(0)    # WS-FACTOR 9(5)V9(9)
total = Decimal(0)     # WS-TOTAL  9(9)V99


def amort(p: dict) -> None:
    """CALL 'AMORT' USING LOAN-PARMS."""
    global mrate, factor, total
    p["LP-RETURN-CODE"] = Decimal(0)
    if (p["LP-PRINCIPAL"] == 0 or p["LP-TERM-MONTHS"] == 0 or p["LP-TERM-MONTHS"] > 480
            or p["LP-ANNUAL-RATE"] > Decimal("0.5")):
        p["LP-RETURN-CODE"] = Decimal(10)
        p["LP-PAYMENT"] = Decimal("0.00")
        p["LP-TOTAL-INTEREST"] = Decimal("0.00")
        return
    if p["LP-ANNUAL-RATE"] == 0:
        p["LP-PAYMENT"] = lbrt.store(p["LP-PRINCIPAL"] / p["LP-TERM-MONTHS"], 7, 2, signed=False, rounded=True)
    else:
        mrate = lbrt.store(p["LP-ANNUAL-RATE"] / 12, 1, 9, signed=False)
        factor = Decimal(1)
        i = 1
        while not i > p["LP-TERM-MONTHS"]:
            factor = lbrt.store(factor * (1 + mrate), 5, 9, signed=False)
            i += 1
        denom = factor - 1
        if denom == 0:                                   # divide by zero is a SIZE ERROR
            p["LP-RETURN-CODE"] = Decimal(30)
            p["LP-PAYMENT"] = Decimal("0.00")
        else:
            v = p["LP-PRINCIPAL"] * mrate * factor / denom
            if lbrt.size_error(v, 7, 2, rounded=True):
                p["LP-RETURN-CODE"] = Decimal(30)
                p["LP-PAYMENT"] = Decimal("0.00")
            else:
                p["LP-PAYMENT"] = lbrt.store(v, 7, 2, signed=False, rounded=True)
    total = lbrt.store(p["LP-PAYMENT"] * p["LP-TERM-MONTHS"], 9, 2, signed=False)
    if total > p["LP-PRINCIPAL"]:
        p["LP-TOTAL-INTEREST"] = lbrt.store(total - p["LP-PRINCIPAL"], 7, 2, signed=False)
    else:
        p["LP-TOTAL-INTEREST"] = Decimal("0.00")
