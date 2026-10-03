"""Integrator: assemble the accepted translations into a runnable package, replay whole-program
scenarios on BOTH the original (GnuCOBOL) and the package, and write the migration report.

The scenario diff is the last, end-to-end check: unit tests prove each module in isolation, the
scenarios prove the assembled package (real imports between modules, the package entry point)
behaves like the original system on realistic inputs. Verdicts use the same deterministic
comparison as the verifier.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.base import LanguageAdapter
from ..config import Settings
from ..db import Blackboard, Status
from ..graph import MigrationPlan
from .verifier import diff_case

REPORT_MD = "REPORT.md"
REPORT_JSON = "report.json"


@dataclass
class ScenarioResult:
    name: str
    module: str
    passed: bool | None            # None: skipped (module not migrated / toolchain missing)
    lines: int = 0
    exit_code: int | None = None
    diff: dict[str, Any] | None = None
    note: str | None = None


@dataclass
class IntegrationResult:
    package_dir: Path | None
    report_path: Path
    json_path: Path
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def scenarios_ok(self) -> bool:
        return all(s.passed is not False for s in self.scenarios)


def package_name(adapter: LanguageAdapter) -> str:
    """Python package name derived from the source tree's directory name."""
    name = re.sub(r"\W+", "_", Path(adapter.root).resolve().name).strip("_").lower() or "migrated"
    return (name if not name[0].isdigit() else "m_" + name) + "_py"


def accepted_code(adapter: LanguageAdapter, db: Blackboard) -> dict[str, str]:
    """{target module: code} for every module with a stored translation."""
    return {adapter.target_name(m["name"]): m["python_code"] for m in db.modules() if m.get("python_code")}


async def run_scenarios(adapter: LanguageAdapter, db: Blackboard, settings: Settings,
                        package_dir: Path | None) -> list[ScenarioResult]:
    """Replay every adapter scenario on the original and on the package; exact diff."""
    verified = {m for u in db.units() if u["status"] == Status.VERIFIED for m in u["members"]}
    code = accepted_code(adapter, db)
    ok, msg = adapter.toolchain_status()
    out = []
    for name, module, stdin in adapter.scenarios():
        if package_dir is None or adapter.target_name(module) not in code:
            out.append(ScenarioResult(name, module, None, note="module not migrated"))
            continue
        if not ok:
            out.append(ScenarioResult(name, module, None, note=f"original cannot run: {msg}"))
            continue
        golden = await adapter.run_legacy(module, stdin)
        actual = await adapter.run_packaged(package_dir, module, stdin)
        d = diff_case(golden.stdout, golden.exit_code, actual, settings.pipeline.verifier.normalize)
        note = None if module in verified else "module accepted WITHOUT verification (ungated)"
        out.append(ScenarioResult(name, module, d is None, len(golden.stdout.splitlines()), golden.exit_code, d, note))
        db.emit("scenario", None, name=name, module=module, passed=d is None)
    return out


def build_report(adapter: LanguageAdapter, db: Blackboard, plan: MigrationPlan | None,
                 scenarios: list[ScenarioResult] | None = None, package_dir: Path | None = None) -> dict[str, Any]:
    """Everything worth knowing about the run, as JSON-serializable data (read from the blackboard)."""
    units = db.units()
    wave_of = {u: i for i, wave in enumerate(plan.order) for u in wave} if plan else {}
    modules = {m["name"]: m for m in db.modules()}
    lessons = db.lessons()
    report_units = []
    for u in sorted(units, key=lambda u: (wave_of.get(u["name"], 0), u["name"])):
        members = u["members"]
        executable = [m for m in members if modules.get(m, {}).get("kind") != "copybook"]
        attempts = db.attempts(u["name"])
        tests = {m: dict(Counter(t["origin"] for t in db.test_cases(m) if t["origin"] != "hidden"))
                 for m in executable}
        report_units.append({
            "unit": u["name"], "members": members, "wave": wave_of.get(u["name"]),
            "kind": "data-only" if not executable else modules[executable[0]]["kind"],
            "status": u["status"], "error": u.get("error"),
            "attempts": len(attempts),
            "attempt_log": [{"no": a["attempt_no"], "kind": a["kind"], "model": a["model"], "verdict": a["verdict"],
                             "stage": a["stage"], "lessons": a["lessons"]} for a in attempts],
            "model_used": u.get("model_used"),
            "fuzz_rounds_clean": u.get("fuzz_rounds_clean", 0), "fuzz_bugs_found": u.get("fuzz_bugs_found", 0),
            "tests": tests, "tests_total": sum(sum(c.values()) for c in tests.values()),
            "seconds": round(u["finished_at"] - u["started_at"], 2)
            if u.get("finished_at") and u.get("started_at") else None,
            "deps": plan.deps_of(u["name"]) if plan else [],
        })
    calls = db.query("SELECT provider, model, status, cache_hit, COUNT(*) n, SUM(prompt_tokens) pt, "
                     "SUM(completion_tokens) ct, AVG(latency_ms) lat FROM llm_calls "
                     "GROUP BY provider, model, status, cache_hit ORDER BY provider, model")
    by_role = db.query("SELECT role, COUNT(*) n FROM llm_calls WHERE status='ok' GROUP BY role ORDER BY n DESC")
    status_counts = Counter(u["status"] for u in report_units if u["kind"] != "data-only")
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(adapter.root), "languages": f"{adapter.source_language} -> {adapter.target_language}",
        "package": str(package_dir) if package_dir else None,
        "summary": {
            "units": sum(status_counts.values()), "data_only_units": sum(r["kind"] == "data-only" for r in report_units),
            "status": dict(status_counts), "verified": status_counts.get(Status.VERIFIED.value, 0),
            "attempts": sum(r["attempts"] for r in report_units),
            "repaired_units": sum(1 for r in report_units if r["status"] == Status.VERIFIED and r["attempts"] > 1),
            "tests": sum(r["tests_total"] for r in report_units),
            "fuzz_bugs_found": sum(r["fuzz_bugs_found"] for r in report_units),
            "lessons": len(lessons),
            "scenarios": {"total": len(scenarios or []), "passed": sum(s.passed is True for s in scenarios or []),
                          "failed": sum(s.passed is False for s in scenarios or []),
                          "skipped": sum(s.passed is None for s in scenarios or [])},
        },
        "units": report_units,
        "scenarios": [s.__dict__ for s in scenarios or []],
        "lessons": [{"id": les["id"], "text": les["text"], "tags": les["tags"], "source_unit": les["source_unit"],
                     "uses": les["uses"]} for les in lessons],
        "llm_usage": [dict(r) for r in calls],
        "llm_by_role": {r["role"]: r["n"] for r in by_role},
    }


def _md_table(header: list[str], rows: list[list[Any]]) -> str:
    def cell(v: Any) -> str:
        return ("" if v is None else str(v)).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def render_markdown(rep: dict[str, Any]) -> str:
    """Human-readable REPORT.md from build_report() data."""
    s = rep["summary"]
    sc = s["scenarios"]
    out = ["# LegacyBridge migration report", "",
           f"- Source: `{rep['source']}` ({rep['languages']})",
           f"- Generated: {rep['generated_at']}",
           f"- Package: `{rep['package']}`" if rep["package"] else "- Package: not written", "",
           "## Summary", "",
           f"- **{s['verified']}/{s['units']} units VERIFIED** by differential testing against the original "
           f"(+{s['data_only_units']} data-only copybook units)",
           f"- Status counts: {', '.join(f'{k}={v}' for k, v in sorted(s['status'].items()))}",
           f"- {s['attempts']} translation/repair attempts; {s['repaired_units']} units needed repair",
           f"- {s['tests']} golden tests recorded; adversarial fuzzing found {s['fuzz_bugs_found']} counterexample(s)",
           f"- {s['lessons']} lessons learned",
           f"- End-to-end scenarios: {sc['passed']}/{sc['total']} identical"
           + (f", {sc['failed']} DIFFER" if sc["failed"] else "") + (f", {sc['skipped']} skipped" if sc["skipped"] else ""),
           "", "## Units", ""]
    rows = []
    for u in rep["units"]:
        if u["kind"] == "data-only":
            continue
        kinds: Counter[str] = Counter()
        for c in u["tests"].values():
            kinds.update(c)
        tests = ", ".join(f"{o} {n}" for o, n in sorted(kinds.items()))
        rows.append([u["unit"], u["wave"], u["kind"], u["status"].upper(), u["attempts"], u["model_used"],
                     u["fuzz_bugs_found"], tests, u["seconds"]])
    out.append(_md_table(["unit", "wave", "kind", "status", "attempts", "final model", "fuzz bugs",
                          "tests by origin", "s"], rows))
    blocked = [u for u in rep["units"] if u["status"] not in (Status.VERIFIED.value,) and u["kind"] != "data-only"]
    if blocked:
        out += ["", "### Not verified", ""]
        out += [f"- **{u['unit']}** ({u['status']}): {u['error'] or 'dependency not verified'}" for u in blocked]
    repaired = [u for u in rep["units"] if u["attempts"] > 1]
    if repaired:
        out += ["", "### Repair history", ""]
        for u in repaired:
            steps = " → ".join(f"{a['kind']}#{a['no']}({a['model'] or '?'}, {a['stage']}: {a['verdict']})"
                               for a in u["attempt_log"])
            out.append(f"- **{u['unit']}**: {steps}")
    out += ["", "## End-to-end scenarios", "",
            "Each scenario is a realistic input file run through the ORIGINAL program (GnuCOBOL) and through "
            "the assembled Python package (`python -m <package> PROGRAM < input`); outputs and exit codes "
            "must be identical.", ""]
    if rep["scenarios"]:
        out.append(_md_table(["scenario", "program", "result", "output lines", "exit", "note"], [
            [x["name"], x["module"], {True: "IDENTICAL", False: "DIFFERS", None: "skipped"}[x["passed"]],
             x["lines"], x["exit_code"], x["note"]] for x in rep["scenarios"]]))
        for x in rep["scenarios"]:
            if x["passed"] is False and x["diff"]:
                d = x["diff"]
                out += ["", f"**{x['name']}** first difference ({d.get('kind')}, line {d.get('line')}):", "```",
                        f"expected: {d.get('expected')!r}", f"actual:   {d.get('actual')!r}", "```"]
    else:
        out.append("_No scenarios defined (put input files in `<source>/scenarios/PROGRAM-name.txt`)._")
    if rep["lessons"]:
        out += ["", "## Lessons learned", ""]
        out += [f"- {les['text']} _(tags: {', '.join(les['tags'])}; from {les['source_unit']}, "
                f"used {les['uses']}x)_" for les in rep["lessons"]]
    out += ["", "## LLM usage", ""]
    if rep["llm_usage"]:
        out.append(_md_table(["provider", "model", "status", "cached", "calls", "prompt tok", "completion tok",
                              "avg ms"], [
            [r["provider"], r["model"], r["status"], "yes" if r["cache_hit"] else "no", r["n"], r["pt"] or 0,
             r["ct"] or 0, round(r["lat"] or 0)] for r in rep["llm_usage"]]))
        out += ["", "Calls by role: " + ", ".join(f"{k} {v}" for k, v in rep["llm_by_role"].items())]
    else:
        out.append("_No LLM calls recorded._")
    return "\n".join(out) + "\n"


def write_report(rep: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Write REPORT.md and report.json into out_dir; returns both paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / REPORT_MD, out_dir / REPORT_JSON
    md.write_text(render_markdown(rep))
    js.write_text(json.dumps(rep, indent=2, default=str))
    return md, js


async def integrate(adapter: LanguageAdapter, db: Blackboard, settings: Settings, plan: MigrationPlan | None,
                    out_dir: Path) -> IntegrationResult:
    """Package accepted code into `out_dir`, diff scenarios end to end, write REPORT.md + report.json."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    code = accepted_code(adapter, db)
    pkg = adapter.package(code, out_dir, package_name(adapter)) if code else None
    scenarios = await run_scenarios(adapter, db, settings, pkg)
    db.set_meta("scenarios", [s.__dict__ for s in scenarios])
    if pkg is not None:
        db.set_meta("package_dir", str(pkg))
    rep = build_report(adapter, db, plan, scenarios, pkg)
    md, js = write_report(rep, out_dir)
    db.emit("integrated", None, package=str(pkg) if pkg else None, scenarios_passed=rep["summary"]["scenarios"]["passed"],
            scenarios_total=len(scenarios))
    return IntegrationResult(pkg, md, js, scenarios)


def stored_scenarios(db: Blackboard) -> list[ScenarioResult]:
    """Scenario results recorded by the last `integrate` run (for `legacybridge report`)."""
    return [ScenarioResult(**s) for s in db.get_meta("scenarios", []) or []]


__all__ = ["integrate", "build_report", "render_markdown", "write_report", "run_scenarios", "stored_scenarios",
           "IntegrationResult", "ScenarioResult", "package_name", "accepted_code"]
