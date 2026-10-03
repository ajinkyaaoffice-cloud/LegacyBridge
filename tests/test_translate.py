"""Python harness, static checks, reference translations, translate -> verify -> repair loop (offline)."""

import asyncio
import shutil

import pytest

from conftest import BANK_DEMO, REPO, requires_cobc
from legacybridge.adapters import get_adapter
from legacybridge.agents.analyzer import analyze
from legacybridge.agents.oracle import build_unit_oracle
from legacybridge.agents.repair import translate_and_repair
from legacybridge.agents.translator import collect_code, executable_members
from legacybridge.agents.verifier import verify_module
from legacybridge.config import load_settings
from legacybridge.db import Blackboard, Status
from legacybridge.llm.client import LLMClient
from legacybridge.sandbox import Sandbox

REF = BANK_DEMO / "reference_py"


@pytest.fixture(scope="module")
def adapter(tmp_path_factory):
    ad = get_adapter("cobol-python", BANK_DEMO, sandbox=Sandbox(), work_dir=tmp_path_factory.mktemp("work"))
    ad.load(ad.discover())
    return ad


def ref_code() -> dict[str, str]:
    return {p.stem: p.read_text() for p in REF.glob("*.py")}


# ---------------------------------------------------------------- static checks / harness

def test_check_target(adapter):
    code = ref_code()
    for m, info in adapter.infos.items():
        if info.kind != "copybook":
            assert adapter.check_target(m, code[adapter.target_name(m)]) is None, m
    assert "missing" in adapter.check_target("FEECALC", "def other(p):\n    pass\n")
    assert "not allowed: os" in adapter.check_target("FEECALC", "import os\ndef feecalc(p):\n    pass\n")
    assert "not allowed: exec" in adapter.check_target("FEECALC", "def feecalc(p):\n    exec('1')\n")
    assert "not allowed" in adapter.check_target("FEECALC", "from . import x\ndef feecalc(p):\n    pass\n")
    assert adapter.check_target("FEECALC", "def feecalc(p:\n").startswith("SyntaxError")


async def test_harness_is_strict(adapter):
    line = adapter.canonical_input("FEECALC", "C|+00000010000|005|+0000000| \n")
    good = "def feecalc(p):\n    from decimal import Decimal\n    p['FP-FEE'] = Decimal('1.50')\n    p['FP-WAIVED'] = 'N'\n"
    res = await adapter.run_target("FEECALC", {"feecalc": good}, line)
    assert res.exit_code == 0 and res.stdout.split("|")[3] == "+0000150", res.stdout + res.stderr
    for bad in ("1.5", "Decimal('1.505')", "Decimal('123456')"):      # float, too many decimals, too big
        code = good.replace("Decimal('1.50')", bad)
        res = await adapter.run_target("FEECALC", {"feecalc": code}, line)
        assert res.stdout.split("|")[3] == "!" * 8, (bad, res.stdout)
    res = await adapter.run_target("FEECALC", {"feecalc": good.replace("'N'", "'NO'")}, line)
    assert res.stdout.rstrip("\n").split("|")[4] == "!"                # alnum longer than PIC X


async def test_harness_return_code_and_main(adapter):
    code = "def feecalc(p):\n    return 7\n"
    res = await adapter.run_target("FEECALC", {"feecalc": code}, "C|+00000010000|005|+0000000| \n")
    assert res.exit_code == 7
    main = "import sys\nimport lbrt\n\ndef main():\n    for r in lbrt.read_records(sys.stdin, 44):\n        lbrt.display(r[:3])\n    return 2\n"
    res = await adapter.run_target("INTPOST", {"intpost": main}, "PAB\nPCD\n")
    assert (res.stdout, res.exit_code) == ("PAB\nPCD\n", 2)


async def test_translated_code_cannot_touch_network_or_main_process(adapter):
    code = "import sys\n\ndef main():\n    print(sys.flags.isolated, sys.flags.no_site)\n    return 0\n"
    res = await adapter.run_target("INTPOST", {"intpost": code}, "")
    assert res.stdout.strip() == "1 1"                       # -I -S: isolated, no site-packages


def test_collect_code_maps_blocks(adapter):
    text = "```python\n# module: ACCTVAL\nA = 1\n```\nnoise\n```python\n# module: loanchk\nB = 2\n```"
    out = collect_code(adapter, ["ACCTVAL", "LOANCHK"], text)
    assert out["acctval"].strip().endswith("A = 1") and out["loanchk"].strip().endswith("B = 2")
    out = collect_code(adapter, ["ACCTVAL", "LOANCHK"], "```python\n# module: LOANCHK\nC = 3\n```",
                       previous={"acctval": "OLD", "loanchk": "OLD"})
    assert out["acctval"] == "OLD" and "C = 3" in out["loanchk"]
    assert collect_code(adapter, ["FEECALC"], "```python\ndef feecalc(p):\n    pass\n```")["feecalc"]


# ---------------------------------------------------------------- against GnuCOBOL

@pytest.fixture(scope="module")
def oracle_db(tmp_path_factory, adapter):
    if shutil.which("cobc") is None:
        pytest.skip("GnuCOBOL (cobc) not installed")
    settings = load_settings(REPO / "config", dry_run=True)
    db = Blackboard(tmp_path_factory.mktemp("state") / "state.db")
    plan, _ = analyze(adapter, db)
    llm = LLMClient(settings, db, mock_brain=adapter.mock_brain())

    async def build():
        for u, ms in plan.units.items():
            if db.unit(u)["status"] != Status.VERIFIED:
                await build_unit_oracle(adapter, db, llm, settings, u, ms)
    asyncio.run(build())
    return db, plan, llm, settings


@requires_cobc
async def test_reference_translations_verify(adapter, oracle_db):
    db, plan, _, settings = oracle_db
    code = ref_code()
    for m, info in adapter.infos.items():
        if info.kind == "copybook":
            continue
        rep = await verify_module(adapter, m, code, db.test_cases(m), settings.pipeline.verifier.normalize)
        assert rep.ok, rep.summary()


@requires_cobc
async def test_loop_offline_all_units(adapter, oracle_db):
    db, plan, llm, settings = oracle_db
    dep: dict[str, str] = {}
    history = {}
    for wave in plan.order:
        for u in wave:
            ms = executable_members(db, plan.units[u])
            if not ms:
                continue
            tests = {m: db.test_cases(m, ["llm", "property", "random"]) for m in ms}
            r = await translate_and_repair(adapter, db, llm, settings, u, ms, dep_code=dep, tests=tests)
            assert r.ok, (u, r.error)
            dep.update(r.code)
            history[u] = [h["role"] for h in r.history]
    assert history["FEECALC"] == ["translator"]
    assert history["FMTUTIL"] == ["translator", "repair"]
    assert history["BALUPD"] == ["translator", "repair", "repair_escalated"]   # escalation after 2 failures
    attempts = db.attempts("BALUPD")
    assert [a["verdict"] for a in attempts] == ["fail", "fail", "pass"]
    assert db.query("SELECT COUNT(*) c FROM verification_results")[0]["c"] > 0


@requires_cobc
async def test_lesson_prevents_known_bug_and_budget_blocks(adapter, oracle_db):
    db, plan, llm, settings = oracle_db
    tests = {"STMTRPT": db.test_cases("STMTRPT", ["llm", "property", "random"])}
    dep = ref_code()
    dep.pop("stmtrpt")
    unit = plan.unit_of["STMTRPT"]
    r = await translate_and_repair(adapter, db, llm, settings, unit, ["STMTRPT"], dep_code=dep, tests=tests,
                                   max_attempts=1)
    assert not r.ok and r.attempts == 1 and "STMTRPT" in r.error
    lesson = {"id": 1, "tags": ["display-trailing-spaces"], "text": "pad DISPLAYed fields"}
    r = await translate_and_repair(adapter, db, llm, settings, unit, ["STMTRPT"], dep_code=dep, tests=tests,
                                   lessons=[lesson], max_attempts=1)
    assert r.ok and r.attempts == 1
