"""Parser, dependency graph and analyzer on the bank_demo corpus."""

import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from conftest import BANK_DEMO, requires_cobc
from legacybridge.adapters import get_adapter
from legacybridge.adapters.cobol_python.parser import flatten, parse_copybook, parse_program, pic_info
from legacybridge.agents.analyzer import analyze, summarize_all
from legacybridge.db import Blackboard, Status
from legacybridge.graph import build_plan
from legacybridge.llm.client import LLMClient

SRC = BANK_DEMO / "src"
COPY = [BANK_DEMO / "copybooks"]


def test_pic_info():
    p = pic_info("S9(9)V99")
    assert (p.kind, p.int_digits, p.scale, p.signed) == ("numeric", 9, 2, True)
    assert pic_info("X(20)").kind == "alnum"
    assert pic_info("ZZZ,ZZ9.99CR").kind == "edited"


def test_parse_subprogram_linkage_and_calls():
    info = parse_program(SRC / "INTCALC.cbl", COPY)
    assert info.kind == "subprogram"
    assert info.calls == ["DATEUTIL"]
    assert set(info.copies) == {"DATEPARM", "INTPARM"}
    (parm,) = info.linkage_params()
    fields = {f.name: f for f in flatten(parm)}
    assert fields["IP-PRINCIPAL"].usage == "COMP-3"
    assert (fields["IP-PRINCIPAL"].int_digits, fields["IP-PRINCIPAL"].scale) == (9, 2)
    assert fields["IP-METHOD"].length == 1
    acct = {f.name: f for f in flatten(parse_copybook(COPY[0] / "ACCTREC.cpy").working_storage[0])}
    assert set(acct["ACCT-TYPE"].values_88) == {"C", "S", "L"}
    assert {"rounded", "size_error", "comp3"} <= set(info.features)


def test_parse_main_program():
    info = parse_program(SRC / "BALUPD.cbl", COPY)
    assert info.kind == "program" and not info.using
    assert info.calls == ["ACCTVAL", "FEECALC"]
    assert "file_io" in info.features
    assert any(p.name.startswith("0000") for p in info.paragraphs)


def test_graph_scc_and_waves():
    mods = {n: {"kind": "subprogram"} for n in "ABCDE"}
    plan = build_plan(mods, [("A", "B", "call"), ("B", "C", "call"), ("C", "B", "call"), ("C", "D", "call"),
                             ("E", "X", "call")])
    assert plan.cycles == [["B", "C"]]
    assert plan.unit_of["B"] == plan.unit_of["C"] == "B+C"
    assert plan.order[0] == ["D", "X"]
    assert plan.order[1:] == [["B+C", "E"], ["A"]]
    assert plan.external == ["X"]
    assert plan.transitive_dependents("D") == {"B+C", "A"}


def test_analyze_bank_demo(tmp_path):
    db = Blackboard(tmp_path / "s.db")
    ad = get_adapter("cobol-python", BANK_DEMO)
    plan, modules = analyze(ad, db)
    names = {m.name for m in modules}
    assert {"BALUPD", "INTPOST", "LOANSCHD", "STMTRPT", "ACCTREC"} <= names
    assert len([m for m in modules if m.executable]) == 11
    assert plan.cycles == [["ACCTVAL", "LOANCHK"]]
    waves = [set(w) for w in plan.order]
    assert {"AMORT", "DATEUTIL", "FEECALC", "FMTUTIL"} == waves[1]
    assert {"ACCTVAL+LOANCHK", "INTCALC", "STMTRPT"} == waves[2]
    assert {"BALUPD", "INTPOST", "LOANSCHD"} == waves[3]
    units = {u["name"]: u for u in db.units()}
    assert units["ACCTREC"]["status"] == Status.VERIFIED          # copybooks are data-only
    assert units["ACCTVAL+LOANCHK"]["status"] == Status.PENDING
    assert ("BALUPD", "ACCTVAL", "call") in {(e["src"], e["dst"], e["kind"]) for e in db.edges()}
    # idempotent / resume-safe: statuses survive re-analysis
    db.set_status("AMORT", Status.VERIFIED)
    analyze(ad, db)
    assert db.unit("AMORT")["status"] == Status.VERIFIED
    total = sum(len(s.source.splitlines()) for s in modules)
    assert 800 <= total <= 1600


async def test_summaries_offline(tmp_path, settings_factory):
    db = Blackboard(tmp_path / "s.db")
    ad = get_adapter("cobol-python", BANK_DEMO)
    analyze(ad, db)
    settings = settings_factory(dry_run=True)
    out = await summarize_all(ad, db, LLMClient(settings, db, mock_brain=ad.mock_brain()))
    assert len(out) == 11
    assert "DATEUTIL" in db.module("INTCALC")["summary"]


def test_cli_analyze(tmp_path, monkeypatch):
    from legacybridge.cli import app

    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["analyze", str(BANK_DEMO), "--state", str(tmp_path / "st")])
    assert res.exit_code == 0, res.output
    assert "ACCTVAL <-> LOANCHK" in res.output and "L3:" in res.output


@requires_cobc
@pytest.mark.cobc
def test_corpus_compiles():
    for path in sorted(SRC.glob("*.cbl")):
        r = subprocess.run([shutil.which("cobc"), "-fsyntax-only", "-I", str(COPY[0]), str(path)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{path.name}: {r.stderr}"
