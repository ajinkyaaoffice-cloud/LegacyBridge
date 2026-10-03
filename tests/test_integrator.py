"""Integrator: packaging, end-to-end scenario diff against the original, REPORT.md / report.json, `report` CLI."""

import json

import pytest
from typer.testing import CliRunner

from conftest import BANK_DEMO, requires_cobc
from legacybridge.adapters import get_adapter
from legacybridge.agents.analyzer import analyze
from legacybridge.agents.integrator import build_report, integrate, package_name, render_markdown
from legacybridge.db import Blackboard, Status
from legacybridge.sandbox import Sandbox

REF = BANK_DEMO / "reference_py"


@pytest.fixture
def adapter(tmp_path):
    ad = get_adapter("cobol-python", BANK_DEMO, sandbox=Sandbox(), work_dir=tmp_path / "work")
    ad.load(ad.discover())
    return ad


def seed_verified(adapter, db, broken: dict[str, tuple[str, str]] | None = None):
    """Store the reference translations as if every unit had been verified."""
    plan, _ = analyze(adapter, db)
    for m in db.modules():
        if m["kind"] == "copybook":
            continue
        code = (REF / f"{adapter.target_name(m['name'])}.py").read_text()
        if broken and m["name"] in broken:
            code = code.replace(*broken[m["name"]])
        db.set_module_fields(m["name"], python_code=code)
    for u in db.units():
        db.set_status(u["name"], Status.VERIFIED, model_used="mock/mock")
    return plan


def test_scenarios_are_discovered(adapter):
    sc = adapter.scenarios()
    assert sorted(m for _, m, _ in sc) == ["BALUPD", "INTPOST", "LOANSCHD", "STMTRPT"]
    for _, module, stdin in sc:
        assert adapter.validate_input(module, stdin) is None
    assert package_name(adapter) == "bank_demo_py"


def test_report_renders_without_toolchain(tmp_path, adapter):
    db = Blackboard(tmp_path / "s.db")
    plan, _ = analyze(adapter, db)
    db.set_status("BALUPD", Status.BLOCKED, error="repair budget exhausted")
    db.add_lesson("COMPUTE without ROUNDED truncates.", ["rounded"], "INTCALC")
    rep = build_report(adapter, db, plan)
    assert rep["summary"]["verified"] == 0 and rep["summary"]["status"]["blocked"] == 1
    md = render_markdown(rep)
    assert "**BALUPD** (blocked): repair budget exhausted" in md
    assert "COMPUTE without ROUNDED truncates." in md and "No scenarios defined" in md
    json.dumps(rep, default=str)


@requires_cobc
async def test_integrate_packages_and_diffs_scenarios(tmp_path, adapter, settings_factory):
    settings = settings_factory(dry_run=True)
    db = Blackboard(tmp_path / "s.db")
    plan = seed_verified(adapter, db)
    res = await integrate(adapter, db, settings, plan, tmp_path / "out")
    assert res.package_dir == tmp_path / "out" / "bank_demo_py"
    assert (res.package_dir / "__main__.py").exists() and (res.package_dir / "lbrt.py").exists()
    assert [s.passed for s in res.scenarios] == [True] * 4 and res.scenarios_ok
    rep = json.loads(res.json_path.read_text())
    assert rep["summary"]["verified"] == 10 and rep["summary"]["scenarios"]["passed"] == 4
    assert "End-to-end scenarios: 4/4 identical" in res.report_path.read_text()
    assert any(e["type"] == "integrated" for e in db.events_after(0, limit=10000))


@requires_cobc
async def test_scenario_diff_catches_integration_bug(tmp_path, adapter, settings_factory):
    """A bug no unit test saw (here: in a dependency) must surface in the end-to-end diff."""
    settings = settings_factory(dry_run=True)
    db = Blackboard(tmp_path / "s.db")
    plan = seed_verified(adapter, db, broken={"FEECALC": ('monthly_fee = Decimal("12.00")',
                                                         'monthly_fee = Decimal("11.00")')})
    res = await integrate(adapter, db, settings, plan, tmp_path / "out")
    by = {s.module: s for s in res.scenarios}
    assert by["BALUPD"].passed is False and by["BALUPD"].diff["kind"] == "stdout"
    assert by["INTPOST"].passed is True
    assert not res.scenarios_ok
    assert "DIFFERS" in res.report_path.read_text()


@requires_cobc
def test_report_cli(tmp_path, adapter):
    from legacybridge.cli import app
    state = tmp_path / "state"
    db = Blackboard(state / "state.db")
    db.set_meta("source_root", str(BANK_DEMO.resolve()))
    seed_verified(adapter, db)
    db.close()
    runner = CliRunner()
    res = runner.invoke(app, ["report", str(BANK_DEMO), "--state", str(state)])
    assert res.exit_code == 0, res.output
    assert "4/4 identical" in res.output and (state / "report" / "REPORT.md").exists()
    res = runner.invoke(app, ["report", str(BANK_DEMO), "--state", str(state), "--no-scenarios",
                              "--out", str(tmp_path / "r2")])
    assert res.exit_code == 0, res.output
    assert "4/4 identical" in (tmp_path / "r2" / "REPORT.md").read_text()
