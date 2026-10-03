"""`legacybridge eval`: compare two baselines with the full pipeline on a HELD-OUT test suite.

Configurations (same prompts, same models; only the feedback machinery differs):

- baseline_a  single-shot translation. One attempt, no repair, no fuzzing, no lessons, and no
              verification gate: whatever the model returns is accepted.
- baseline_b  differential tests + repair loop with escalation. Gated, but no fuzzing and no lessons.
- full        the full pipeline: tests, repair, adversarial fuzzing and lessons.

Each configuration migrates the corpus into its own fresh state database. Afterwards every
accepted module is run on a hidden suite. That suite is generated once from an independent
seed and recorded from the ORIGINAL program; any input the pipeline itself saw is removed. The
assembled package is also checked against the end-to-end scenarios. No LLM is involved in scoring.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters import get_adapter
from ..adapters.base import LanguageAdapter
from ..agents.analyzer import analyze
from ..agents.integrator import accepted_code, integrate
from ..agents.oracle import build_hidden_suite
from ..agents.verifier import verify_module
from ..config import Settings
from ..db import Blackboard, Status
from ..llm.client import LLMClient
from ..pipeline import Pipeline, PipelineOptions
from ..sandbox import Sandbox
from ..scheduler import Scheduler

HIDDEN_SEED = 20_240_917     # independent of pipeline.yaml oracle.seed


@dataclass
class EvalConfig:
    name: str
    title: str
    options: PipelineOptions


CONFIGS: dict[str, EvalConfig] = {c.name: c for c in [
    EvalConfig("baseline_a", "A: single-shot (no tests, no repair, ungated)",
               PipelineOptions(repair=False, max_attempts=1, fuzz=False, lessons=False, gate=False)),
    EvalConfig("baseline_b", "B: tests + repair loop (no fuzzing, no lessons)",
               PipelineOptions(repair=True, fuzz=False, lessons=False, gate=True)),
    EvalConfig("full", "Full: tests + repair + adversarial fuzzing + lessons", PipelineOptions()),
]}


@dataclass
class EvalRun:
    root: Path
    run_dir: Path
    settings: Settings
    hidden_n: int = 60
    concurrency: int | None = None
    configs: list[str] = field(default_factory=lambda: list(CONFIGS))
    adapter_name: str = "cobol-python"

    def adapter(self) -> LanguageAdapter:
        ad = get_adapter(self.adapter_name, self.root, sandbox=Sandbox(self.settings.pipeline.sandbox),
                         work_dir=self.run_dir / "work")            # shared compile cache across configs
        ad.load(ad.discover())
        return ad


async def build_hidden(run: EvalRun) -> dict[str, list[dict[str, Any]]]:
    """Held-out tests per executable module, recorded once from the original program."""
    ad = run.adapter()
    for suffix in ("", "-wal", "-shm"):                 # rebuilt every run (n / seed may differ)
        Path(f"{run.run_dir / 'hidden.db'}{suffix}").unlink(missing_ok=True)
    db = Blackboard(run.run_dir / "hidden.db")
    try:
        analyze(ad, db)
        out = {}
        for m in db.modules():
            if m["kind"] == "copybook" or ad.testable(m["name"]):
                continue
            await build_hidden_suite(ad, db, m["name"], run.hidden_n, HIDDEN_SEED)
            out[m["name"]] = db.test_cases(m["name"], ["hidden"])
        return out
    finally:
        db.close()


def llm_usage(db: Blackboard) -> dict[str, Any]:
    r = db.one("SELECT COUNT(*) n, SUM(cache_hit) cached, SUM(CASE WHEN status='ok' THEN 0 ELSE 1 END) failed, "
               "SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct FROM llm_calls")
    models = db.query("SELECT model, COUNT(*) n FROM llm_calls WHERE status='ok' GROUP BY model ORDER BY n DESC")
    return {"calls": r["n"] or 0, "cached": r["cached"] or 0, "failed": r["failed"] or 0,
            "prompt_tokens": r["pt"] or 0, "completion_tokens": r["ct"] or 0,
            "models": {m["model"]: m["n"] for m in models}}


async def run_config(run: EvalRun, cfg: EvalConfig, hidden: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Migrate the corpus with one configuration and score it on the hidden suite + scenarios."""
    cdir = run.run_dir / cfg.name
    if cdir.exists():
        shutil.rmtree(cdir)
    db = Blackboard(cdir / "state.db")
    try:
        ad = run.adapter()
        plan, _ = analyze(ad, db)
        db.set_meta("source_root", str(run.root.resolve()))
        db.set_meta("eval_config", cfg.name)
        llm = LLMClient(run.settings, db, mock_brain=ad.mock_brain() if run.settings.dry_run else None)
        pipe = Pipeline(ad, db, llm, run.settings, plan, cfg.options)
        t0 = time.time()
        counts = await Scheduler(pipe, run.concurrency).run()
        wall = time.time() - t0
        code = accepted_code(ad, db)
        status_of = {m: u["status"] for u in db.units() for m in u["members"]}
        modules = {}
        for m, tests in sorted(hidden.items()):
            seen = {t["input_hash"] for t in db.test_cases(m)}
            held_out = [t for t in tests if t["input_hash"] not in seen]
            row: dict[str, Any] = {"status": status_of.get(m), "accepted": ad.target_name(m) in code,
                                   "hidden_total": len(held_out), "hidden_excluded_seen": len(tests) - len(held_out)}
            if row["accepted"]:
                rep = await verify_module(ad, m, code, held_out, run.settings.pipeline.verifier.normalize)
                row["hidden_passed"] = rep.passed
                row["correct"] = rep.ok
                if not rep.ok:
                    ff = rep.failures[0].diff if rep.failures else {"static_error": rep.static_error}
                    row["first_failure"] = {k: ff.get(k) for k in ("kind", "line", "expected", "actual", "static_error")
                                            if ff and ff.get(k) is not None}
            else:
                row["hidden_passed"], row["correct"] = 0, False
            modules[m] = row
        integ = await integrate(ad, db, run.settings, plan, cdir / "out")
        units = [u for u in db.units() if any(m in hidden for m in u["members"])]
        attempts = sum(len(db.attempts(u["name"])) for u in units)
        n = len(modules)
        verified = [m for m, r in modules.items() if r["status"] == Status.VERIFIED]
        return {
            "config": cfg.name, "title": cfg.title, "options": cfg.options.__dict__,
            "seconds": round(wall, 1), "unit_status": counts,
            "modules": modules,
            "summary": {
                "modules": n,
                "accepted": sum(r["accepted"] for r in modules.values()),
                "verified": len(verified),
                "correct_on_hidden": sum(r["correct"] for r in modules.values()),
                "accepted_but_wrong": sum(r["accepted"] and not r["correct"] for r in modules.values()),
                "verified_but_wrong": sum(not modules[m]["correct"] for m in verified),
                "hidden_pass_rate": round(sum(r["hidden_passed"] for r in modules.values())
                                          / max(1, sum(r["hidden_total"] for r in modules.values())), 4),
                "scenarios_identical": sum(s.passed is True for s in integ.scenarios),
                "scenarios_total": len(integ.scenarios),
                "attempts": attempts,
                "fuzz_bugs_found": sum(int(u.get("fuzz_bugs_found") or 0) for u in units),
                "lessons": len(db.lessons()),
            },
            "scenarios": [{"name": s.name, "module": s.module, "passed": s.passed} for s in integ.scenarios],
            "llm": llm_usage(db),
        }
    finally:
        db.close()


async def run_eval(run: EvalRun) -> dict[str, Any]:
    """All configurations, one after another (they share the compile cache and LLM response cache)."""
    run.run_dir.mkdir(parents=True, exist_ok=True)
    hidden = await build_hidden(run)
    results = [await run_config(run, CONFIGS[name], hidden) for name in run.configs]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "dry-run (MockProvider)" if run.settings.dry_run else "live (free-tier APIs)",
        "corpus": str(run.root), "hidden_seed": HIDDEN_SEED, "hidden_per_module": run.hidden_n,
        "hidden_tests": sum(len(v) for v in hidden.values()),
        "results": results,
    }


def render_markdown(ev: dict[str, Any]) -> str:
    """EVAL table + per-module breakdown."""
    res = ev["results"]
    out = [f"# LegacyBridge evaluation — {ev['mode']}", "",
           f"- Corpus: `{ev['corpus']}`; generated {ev['generated_at']}",
           f"- Hidden suite: {ev['hidden_tests']} tests recorded from the ORIGINAL programs "
           f"(seed {ev['hidden_seed']}, up to {ev['hidden_per_module']} per module); inputs any configuration "
           "saw during migration are excluded from its score.", ""]
    if ev["mode"].startswith("dry-run"):
        out += ["> **Dry run.** The MockProvider replays reference translations with a fixed catalogue of "
                "realistic bugs, so these numbers measure whether the pipeline machinery (gate, repair, "
                "escalation, fuzzing, lessons) catches and fixes them. They are not a measure of LLM "
                "quality. Run `legacybridge eval` with API keys for live numbers.", ""]
    metrics = [
        ("Modules correct on hidden suite", lambda s: f"{s['correct_on_hidden']}/{s['modules']}"),
        ("Hidden-test pass rate", lambda s: f"{100 * s['hidden_pass_rate']:.1f}%"),
        ("Modules accepted (code shipped)", lambda s: f"{s['accepted']}/{s['modules']}"),
        ("…of which wrong on hidden suite", lambda s: str(s["accepted_but_wrong"])),
        ("Modules VERIFIED by the gate", lambda s: f"{s['verified']}/{s['modules']}"),
        ("…VERIFIED but wrong (false accept)", lambda s: str(s["verified_but_wrong"])),
        ("End-to-end scenarios identical", lambda s: f"{s['scenarios_identical']}/{s['scenarios_total']}"),
        ("Translation/repair attempts", lambda s: str(s["attempts"])),
        ("Fuzz counterexamples found", lambda s: str(s["fuzz_bugs_found"])),
        ("Lessons learned", lambda s: str(s["lessons"])),
    ]
    out.append("| metric | " + " | ".join(r["title"] for r in res) + " |")
    out.append("|---|" + "---|" * len(res))
    for label, f in metrics:
        out.append(f"| {label} | " + " | ".join(f(r["summary"]) for r in res) + " |")
    out.append("| LLM calls (cached) | " + " | ".join(f"{r['llm']['calls']} ({r['llm']['cached']})" for r in res) + " |")
    out.append("| Tokens in / out | " + " | ".join(
        f"{r['llm']['prompt_tokens']:,} / {r['llm']['completion_tokens']:,}" for r in res) + " |")
    out.append("| Wall time (s) | " + " | ".join(str(r["seconds"]) for r in res) + " |")
    out += ["", "## Per module (hidden tests passed / held-out total)", "",
            "| module | " + " | ".join(r["config"] for r in res) + " |", "|---|" + "---|" * len(res)]
    for m in res[0]["modules"]:
        cells = []
        for r in res:
            row = r["modules"][m]
            if not row["accepted"]:
                cells.append(f"not accepted ({row['status']})")
                continue
            mark = "✓" if row["correct"] else "✗"
            cells.append(f"{mark} {row['hidden_passed']}/{row['hidden_total']}"
                         + ("" if row["status"] == Status.VERIFIED else " (shipped unverified)"))
        out.append(f"| {m} | " + " | ".join(cells) + " |")
    wrong = [(r["config"], m, row) for r in res for m, row in r["modules"].items()
             if row["accepted"] and not row["correct"] and row.get("first_failure")]
    if wrong:
        out += ["", "## Residual bugs (first hidden-test divergence)", ""]
        for cfg, m, row in wrong:
            ff = row["first_failure"]
            out.append(f"- **{cfg} / {m}**: {ff.get('kind')} line {ff.get('line')}: expected "
                       f"`{ff.get('expected')!r}` got `{ff.get('actual')!r}`" if "expected" in ff
                       else f"- **{cfg} / {m}**: {ff}")
    return "\n".join(out) + "\n"


def write_results(ev: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """eval/results/<mode>-<timestamp>.{json,md} plus latest-<mode>.{json,md}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = "dryrun" if ev["mode"].startswith("dry-run") else "live"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    md_text, js_text = render_markdown(ev), json.dumps(ev, indent=2, default=str)
    for base in (f"{mode}-{stamp}", f"latest-{mode}"):
        (out_dir / f"{base}.md").write_text(md_text)
        (out_dir / f"{base}.json").write_text(js_text)
    return out_dir / f"latest-{mode}.md", out_dir / f"latest-{mode}.json"
