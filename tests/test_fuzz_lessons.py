"""Comparison-threshold harvesting, lesson selection/reflection, adversarial fuzzing (offline)."""

import random
from decimal import Decimal

import pytest

from conftest import BANK_DEMO, REPO, requires_cobc
from legacybridge.adapters import get_adapter
from legacybridge.adapters.cobol_python.inputs import InputGenerator, harvest_literals, mutation_cases, property_cases
from legacybridge.agents.analyzer import analyze
from legacybridge.agents.reflector import reflect, select_lessons
from legacybridge.config import load_settings
from legacybridge.db import Blackboard
from legacybridge.llm.client import LLMClient
from legacybridge.pipeline import Pipeline
from legacybridge.sandbox import Sandbox
from legacybridge.scheduler import Scheduler


@pytest.fixture(scope="module")
def adapter(tmp_path_factory):
    ad = get_adapter("cobol-python", BANK_DEMO, sandbox=Sandbox(), work_dir=tmp_path_factory.mktemp("work"))
    ad.load(ad.discover())
    return ad


def test_numeric_thresholds_are_linked_to_fields(adapter):
    lits = harvest_literals(adapter.sources["FEECALC"])
    assert lits.num_by_field["FP-BALANCE"] == [Decimal("0"), Decimal("300.00"), Decimal("1500.00")]
    assert "ROUNDED" not in lits.num_by_field
    loan = harvest_literals(adapter.sources["LOANCHK"]).num_by_field
    assert Decimal("-500000.00") in loan["ACCT-BALANCE"] and Decimal("0.2500") in loan["ACCT-RATE"]
    gen = InputGenerator(adapter.layout("FEECALC"), lits, random.Random(1))
    rec = adapter.layout("FEECALC").records[0]
    bal = next(f for f in rec.fields if f.name == "FP-BALANCE")
    assert gen.boundaries(bal, rec)[:3] == [Decimal("-0.01"), Decimal("0"), Decimal("0.01")]
    # property cases cover every threshold of FP-BALANCE (1499.99 / 1500.00 / 1500.01 ...)
    texts = "".join(property_cases(gen, 12))
    for v in ("+00000149999", "+00000150000", "+00000150001"):
        assert v in texts
    muts = mutation_cases(gen, [texts.split("\n")[0] + "\n"], 12)
    assert muts and all(adapter.validate_input("FEECALC", m) is None for m in muts)


def test_select_lessons_prefers_matching_features(tmp_path, adapter):
    db = Blackboard(tmp_path / "s.db")
    a = db.add_lesson("about strings", ["string_stmt"], "X")
    b = db.add_lesson("about files", ["file_io", "edited_pic"], "Y")
    c = db.add_lesson("about rounding", ["rounded", "truncation"], "Z")
    got = [les["id"] for les in select_lessons(db, adapter, ["BALUPD"], 2)]
    assert got == [b, c]                      # BALUPD: file_io+edited_pic (2) > truncation (1) > none
    assert [les["id"] for les in select_lessons(db, adapter, ["FMTUTIL"], 1)] == [b]  # tie (1 tag each): newest wins
    db.bump_lesson_uses([a, a])
    assert [les["id"] for les in select_lessons(db, adapter, ["FMTUTIL"], 1)] == [a]


async def test_reflector_creates_tagged_lesson_without_duplicates(tmp_path, adapter, settings_factory):
    settings = settings_factory(dry_run=True)
    db = Blackboard(tmp_path / "s.db")
    analyze(adapter, db)
    good = (BANK_DEMO / "reference_py" / "fmtutil.py").read_text()
    bad = good.replace('out = lbrt.alnum(p["FM-OUT"], 20)', 'out = " " * 20')
    a1 = db.add_attempt("FMTUTIL", 1, "translate", "mock/mock", {"fmtutil": bad})
    db.finish_attempt(a1, "fail", {"FMTUTIL": {"passed": 28, "total": 34}}, "recorded")
    a2 = db.add_attempt("FMTUTIL", 2, "repair", "mock/mock", {"fmtutil": good})
    db.finish_attempt(a2, "pass", {"FMTUTIL": {"passed": 34, "total": 34}}, "recorded")
    llm = LLMClient(settings, db, mock_brain=adapter.mock_brain())
    ids = await reflect(adapter, db, llm, "FMTUTIL", ["FMTUTIL"])
    assert len(ids) == 1
    les = db.lessons()[0]
    assert "string-overwrite" in les["tags"] and "string_stmt" in les["tags"] and les["source_unit"] == "FMTUTIL"
    assert await reflect(adapter, db, llm, "FMTUTIL", ["FMTUTIL"]) == []        # same lesson is not stored twice


@requires_cobc
async def test_fuzzer_finds_bug_oracle_missed_and_lessons_carry_over(tmp_path, adapter):
    settings = load_settings(REPO / "config", dry_run=True)
    db = Blackboard(tmp_path / "state.db")
    plan, _ = analyze(adapter, db)
    llm = LLMClient(settings, db, mock_brain=adapter.mock_brain())
    pipe = Pipeline(adapter, db, llm, settings, plan)
    counts = await Scheduler(pipe, concurrency=1).run()     # deterministic order: STMTRPT before BALUPD
    assert counts == {"verified": 18}

    st = db.unit("STMTRPT")
    assert st["fuzz_bugs_found"] >= 1 and st["fuzz_rounds_clean"] == settings.pipeline.fuzzer.rounds
    assert db.test_cases("STMTRPT", ["fuzz_llm", "fuzz_mutation"])
    final = db.module("STMTRPT")["python_code"]
    assert "if lines == 10:" in final                         # the page-break bug was repaired
    bugs = [e for e in db.events_after(0, limit=100000) if e["type"] == "fuzz_bug"]
    assert any(e["unit"] == "STMTRPT" for e in bugs)

    tags = {t for les in db.lessons() for t in les["tags"]}
    assert {"string-overwrite", "truncation", "display-trailing-spaces", "counter-boundary"} <= tags
    first = db.attempts("BALUPD")[0]
    assert first["lessons"], "BALUPD's first prompt should carry lessons"
    assert "reason.rstrip()" not in first["code"]["balupd"]   # lesson from STMTRPT prevented this bug
    assert 'acct_rec["ACCT-ID"]' in first["code"]["balupd"]  # the unrelated bug is still made
