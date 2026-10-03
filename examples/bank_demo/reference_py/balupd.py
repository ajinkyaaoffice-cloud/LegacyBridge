# module: BALUPD
"""BALUPD - daily balance update: load/validate accounts, post transactions, charge fees."""

import sys
from decimal import Decimal

import acctval
import feecalc
import lbrt

REC_LEN = 56          # longest FD record (IN-ACCT)
D = Decimal

acct_rec = {"ACCT-ID": " " * 8, "ACCT-NAME": " " * 20, "ACCT-TYPE": " ", "ACCT-BALANCE": D(0),
            "ACCT-RATE": D(0), "ACCT-OPEN-DATE": D(0), "ACCT-STATUS": " "}
val_parms = {"VP-FUNCTION": " ", "VP-RESULT": "  ", "VP-MESSAGE": " " * 30}
fee_parms = {"FP-ACCT-TYPE": " ", "FP-BALANCE": D(0), "FP-TXN-COUNT": D(0), "FP-FEE": D(0), "FP-WAIVED": " "}

rec = " " * REC_LEN
rec_no = D(0)
rejects = D(0)
overflow = "N"
tot_dep = D(0)
tot_wd = D(0)
tot_fees = D(0)
found = 0
reason = " " * 30
table: list[dict] = []      # WS-ENTRY (max 20): id, name, type, balance, txns


def _reject() -> None:
    global rejects
    rejects = lbrt.store(rejects + 1, 5, signed=False)
    lbrt.display("REJECT ", lbrt.fmt_num(rec_no, 5), " ", rec[1:9], " ", reason)


def _find_account() -> None:
    global found
    found = 0
    for i, e in enumerate(table, 1):
        if e["id"] == acct_rec["ACCT-ID"]:
            found = i
            break


def _load_account() -> None:
    global reason
    bal_x, rate_x, date_x = rec[30:42], rec[42:47], rec[47:55]
    if not lbrt.is_numeric(bal_x, sign_separate=True) or not lbrt.is_numeric(rate_x) or not lbrt.is_numeric(date_x):
        reason = lbrt.alnum("BAD ACCOUNT RECORD", 30)
        _reject()
        return
    acct_rec.update({"ACCT-ID": rec[1:9], "ACCT-NAME": rec[9:29], "ACCT-TYPE": rec[29],
                     "ACCT-BALANCE": lbrt.parse_num(bal_x, 2, sign_separate=True),
                     "ACCT-RATE": lbrt.parse_num(rate_x, 4), "ACCT-OPEN-DATE": lbrt.parse_num(date_x),
                     "ACCT-STATUS": rec[55]})
    val_parms["VP-FUNCTION"] = "V"
    acctval.acctval(acct_rec, val_parms)
    if val_parms["VP-RESULT"] != "OK":
        reason = lbrt.alnum(val_parms["VP-RESULT"] + " " + val_parms["VP-MESSAGE"], 30)
        _reject()
    else:
        _add_to_table()


def _add_to_table() -> None:
    global reason
    _find_account()
    if found > 0:
        reason = lbrt.alnum("DUPLICATE ACCOUNT", 30)
        _reject()
    elif len(table) == 20:
        reason = lbrt.alnum("ACCOUNT TABLE FULL", 30)
        _reject()
    else:
        table.append({"id": acct_rec["ACCT-ID"], "name": acct_rec["ACCT-NAME"], "type": acct_rec["ACCT-TYPE"],
                      "balance": acct_rec["ACCT-BALANCE"], "txns": D(0)})


def _post_transaction() -> None:
    global reason
    acct_rec["ACCT-ID"] = rec[1:9]
    amt_x, date_x = rec[10:20], rec[20:28]
    if not lbrt.is_numeric(amt_x, sign_separate=True) or not lbrt.is_numeric(date_x):
        reason = lbrt.alnum("BAD TRANSACTION RECORD", 30)
        _reject()
        return
    amount = lbrt.parse_num(amt_x, 2, sign_separate=True)
    if not amount > 0:
        reason = lbrt.alnum("NON-POSITIVE AMOUNT", 30)
        _reject()
        return
    _find_account()
    if found == 0:
        reason = lbrt.alnum("ACCOUNT NOT ON FILE", 30)
        _reject()
    else:
        _apply(rec[9], amount)


def _apply(code: str, amount: Decimal) -> None:
    global tot_dep, tot_wd, overflow, reason
    e = table[found - 1]
    if code == "D":
        e["balance"] = lbrt.store(e["balance"] + amount, 9, 2)
        if lbrt.size_error(tot_dep + amount, 8, 2):
            overflow = "Y"
        else:
            tot_dep = tot_dep + amount
        e["txns"] = lbrt.store(e["txns"] + 1, 3, signed=False)
    elif code == "W":
        limit = {"C": D("-500.00"), "S": D(0)}.get(e["type"], D("999999999.99"))
        new_bal = lbrt.store(e["balance"] - amount, 9, 2)
        if new_bal < limit:
            reason = lbrt.alnum("INSUFFICIENT FUNDS", 30)
            _reject()
        else:
            e["balance"] = new_bal
            if lbrt.size_error(tot_wd + amount, 8, 2):
                overflow = "Y"
            else:
                tot_wd = tot_wd + amount
            e["txns"] = lbrt.store(e["txns"] + 1, 3, signed=False)
    elif code == "F":
        e["balance"] = lbrt.store(e["balance"] - amount, 9, 2)
        e["txns"] = lbrt.store(e["txns"] + 1, 3, signed=False)
    elif code == "I":
        e["balance"] = lbrt.store(e["balance"] + amount, 9, 2)
        e["txns"] = lbrt.store(e["txns"] + 1, 3, signed=False)
    else:
        reason = lbrt.alnum("UNKNOWN TRANSACTION CODE", 30)
        _reject()


def _end_of_day() -> None:
    global tot_fees
    for e in table:
        fee_parms["FP-ACCT-TYPE"] = e["type"]
        fee_parms["FP-BALANCE"] = e["balance"]
        fee_parms["FP-TXN-COUNT"] = e["txns"]
        feecalc.feecalc(fee_parms)
        fee = fee_parms["FP-FEE"]
        e["balance"] = lbrt.store(e["balance"] - fee, 9, 2)
        tot_fees = lbrt.store(tot_fees + fee, 8, 2)
        lbrt.display("ACCT ", e["id"], " ", e["name"], " BAL=", lbrt.edit(e["balance"], "-ZZZ,ZZZ,ZZ9.99"),
                     " FEE=", lbrt.edit(fee, "ZZ9.99"), " TXNS=", lbrt.edit(e["txns"], "ZZ9"))
    lbrt.display("TOTALS: DEP=", lbrt.edit(tot_dep, "ZZ,ZZZ,ZZ9.99"), " WD=", lbrt.edit(tot_wd, "ZZ,ZZZ,ZZ9.99"),
                 " FEES=", lbrt.edit(tot_fees, "ZZ,ZZZ,ZZ9.99"), " REJECTS=", lbrt.edit(rejects, "ZZZZ9"))
    if overflow == "Y":
        lbrt.display("WARNING: TOTALS OVERFLOWED")


def main() -> int:
    """Run BALUPD over stdin (LINE SEQUENTIAL input file)."""
    global rec, rec_no, reason
    for rec in lbrt.read_records(sys.stdin, REC_LEN):
        rec_no = lbrt.store(rec_no + 1, 5, signed=False)
        if rec[0] == "A":
            _load_account()
        elif rec[0] == "T":
            _post_transaction()
        else:
            reason = lbrt.alnum("UNKNOWN RECORD TYPE", 30)
            _reject()
    _end_of_day()
    return 4 if rejects > 0 else 0
