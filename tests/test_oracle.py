"""I/O layouts, input generators, COBOL driver + oracle (golden runs against GnuCOBOL)."""

import random

import pytest

from conftest import BANK_DEMO, requires_cobc
from legacybridge.adapters import get_adapter
from legacybridge.agents.analyzer import analyze
from legacybridge.agents.oracle import build_hidden_suite, build_unit_oracle
from legacybridge.db import Blackboard
from legacybridge.llm.client import LLMClient
from legacybridge.sandbox import Sandbox


@pytest.fixture(scope="module")
def adapter(tmp_path_factory):
    ad = get_adapter("cobol-python", BANK_DEMO, sandbox=Sandbox(), work_dir=tmp_path_factory.mktemp("work"))
    ad.discover()
    return ad


def test_call_layout_format_and_check(adapter):
    rec = adapter.layout("INTCALC").records[0]
    line = rec.format({"IP-PRINCIPAL": "10000", "IP-ANNUAL-RATE": "0.05", "IP-START-DATE": 20240101,
                       "IP-END-DATE": 20241231, "IP-METHOD": "S", "IP-INTEREST": 0, "IP-DAYS": "-3",
                       "IP-RETURN-CODE": 0})
    assert line == "+00001000000|005000|20240101|20241231|S|+000000000|-00003|00"
    assert rec.check(line, strict_numeric=True) is None
    assert "signed" in rec.check(line.replace("+0000100", " 0000100"), strict_numeric=True)
    with pytest.raises(ValueError):
        rec.format({"IP-ANNUAL-RATE": "12.5"})          # 9V9(5) can't hold 12.5
    with pytest.raises(ValueError):
        rec.format({"IP-START-DATE": -1})               # unsigned
    assert str(rec.fields[0].parse("-00000012345")) == "-123.45"


def test_validate_and_canonical(adapter):
    assert adapter.validate_input("BALUPD", "A" + "x" * 60 + "\n").startswith("line 1: longer")
    assert "printable" in adapter.validate_input("BALUPD", "A\tB\n")
    short = "+00001000000|005000|20240101|20241231|S"     # trailing fields missing -> padded, then invalid
    assert adapter.validate_input("INTCALC", adapter.canonical_input("INTCALC", short))
    assert adapter.canonical_input("BALUPD", "A1\r\nT2") == "A1\nT2\n"


def test_llm_case_conversion(adapter):
    text = adapter.parse_llm_case("STMTRPT", {"records": [
        {"record": "IN-HEADER", "fields": {"IH-ACCT-ID": "AC1", "IH-NAME": "X", "IH-STMT-DATE": 20240131,
                                           "IH-OPEN-BAL": "-5.5"}},
        {"record": "IN-TXN", "fields": {"IT-DATE": "20240102", "IT-CODE": "D", "IT-AMOUNT": 1,
                                        "IT-DESC": "PAY"}}]})
    lines = text.splitlines()
    assert lines[0] == "HAC1     X                   20240131-00000000550"
    assert lines[1].startswith("T20240102D000000100PAY")
    with pytest.raises(ValueError):
        adapter.parse_llm_case("STMTRPT", {"records": [{"record": "NOPE", "fields": {}}]})
    raw = adapter.parse_llm_case("LOANSCHD", {"records": [{"fields": {"IL-PRINCIPAL": {"raw": "   1000000"[:9]}}}]})
    assert raw[9:18] == "   100000"


@pytest.mark.parametrize("module", ["ACCTVAL", "AMORT", "BALUPD", "DATEUTIL", "FEECALC", "FMTUTIL", "INTCALC",
                                    "INTPOST", "LOANCHK", "LOANSCHD", "STMTRPT"])
def test_generators_valid_and_deterministic(adapter, module):
    a = adapter.property_inputs(module, 6, random.Random(5)) + adapter.random_inputs(module, 6, random.Random(6))
    b = adapter.property_inputs(module, 6, random.Random(5)) + adapter.random_inputs(module, 6, random.Random(6))
    assert [x.text for x in a] == [x.text for x in b]
    for gi in a:
        assert adapter.validate_input(module, gi.text) is None, (gi.name, gi.text)
    muts = adapter.mutation_inputs(module, [x.text for x in a], 10, random.Random(7))
    assert muts
    for gi in muts:
        assert adapter.validate_input(module, gi.text) is None, gi.text


def test_driver_mentions_every_field(adapter):
    src = adapter.driver_source("LOANCHK")
    assert "CALL 'LOANCHK' USING ACCT-REC LCHK-PARMS" in src
    assert "MOVE LB-I-0001 TO ACCT-ID OF ACCT-REC" in src
    assert adapter.driver_source("BALUPD") is None


@requires_cobc
async def test_driver_golden_intcalc(adapter):
    line = "+00001000000|005000|20240101|20241231|{}|+000000000|+00000|00\n"
    res = await adapter.run_legacy("INTCALC", "".join(line.format(m) for m in "SMCX"))
    out = res.stdout.splitlines()
    assert res.exit_code == 0
    assert out[0].endswith("|S|+000050000|+00365|00")      # 10000 * 5% * 365/365
    assert out[1].endswith("|M|+000050000|+00360|00")      # 30/360
    assert out[2].split("|")[5] == "+000051267"            # daily compounding (512.67)
    assert out[3].endswith("|99")                          # bad method


@requires_cobc
async def test_oracle_offline_all_units(tmp_path, adapter, settings_factory):
    settings = settings_factory(dry_run=True)
    db = Blackboard(tmp_path / "s.db")
    plan, _ = analyze(adapter, db)
    llm = LLMClient(settings, db, mock_brain=adapter.mock_brain())
    for unit, members in plan.units.items():
        if db.unit(unit)["status"] == "verified":
            continue
        assert await build_unit_oracle(adapter, db, llm, settings, unit, members) > 20
        assert db.unit(unit)["oracle_done"] == 1
    origins = {tc["origin"] for tc in db.test_cases("BALUPD")}
    assert origins == {"llm", "property", "random"}
    # idempotent: a second run adds nothing new
    before = len(db.test_cases("FEECALC"))
    await build_unit_oracle(adapter, db, llm, settings, "FEECALC", ["FEECALC"])
    assert len(db.test_cases("FEECALC")) == before
    assert await build_hidden_suite(adapter, db, "FEECALC", 10, seed=99) > 0
    assert db.test_cases("FEECALC", origins=["hidden"])
