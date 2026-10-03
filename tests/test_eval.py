"""`eval`: baselines vs full pipeline scored on a held-out suite (offline, MockProvider)."""

import json

from typer.testing import CliRunner

from conftest import BANK_DEMO, REPO, requires_cobc
from legacybridge.config import load_settings
from legacybridge.evaluation.runner import EvalRun, render_markdown, run_eval, write_results


def test_eval_rejects_unknown_config():
    from legacybridge.cli import app
    res = CliRunner().invoke(app, ["eval", str(BANK_DEMO), "--dry-run", "--configs", "full,nope"])
    assert res.exit_code == 2 and "Unknown config" in res.output


@requires_cobc
async def test_eval_baselines_vs_full(tmp_path):
    settings = load_settings(REPO / "config", dry_run=True)
    run = EvalRun(root=BANK_DEMO, run_dir=tmp_path / "runs", settings=settings, hidden_n=9,
                  configs=["baseline_a", "baseline_b", "full"])
    ev = await run_eval(run)
    a, b, full = (r["summary"] for r in ev["results"])
    by = {r["config"]: r for r in ev["results"]}
    # A ships every module, including the catalogued bugs; nothing gated it
    assert a["accepted"] == 11 and a["accepted_but_wrong"] >= 4 and a["verified_but_wrong"] == 0
    # B's gate fixes what the oracle tests see, but STMTRPT's page-break bug passes them (false accept)
    assert b["verified"] == 11 and by["baseline_b"]["modules"]["STMTRPT"]["correct"] is False
    assert b["verified_but_wrong"] == 1
    # full: fuzzing found it; every module correct on held-out tests, every scenario identical
    assert full["correct_on_hidden"] == 11 and full["verified_but_wrong"] == 0 and full["fuzz_bugs_found"] >= 1
    assert full["scenarios_identical"] == full["scenarios_total"] == 4
    assert all(row["hidden_total"] > 0 for row in by["full"]["modules"].values())

    md, js = write_results(ev, tmp_path / "results")
    text = md.read_text()
    assert "VERIFIED but wrong" in text and "Dry run." in text and "STMTRPT" in text
    assert json.loads(js.read_text())["results"][2]["config"] == "full"
    assert render_markdown(ev) == text
