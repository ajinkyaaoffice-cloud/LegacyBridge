"""`legacybridge` command-line interface (typer)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .adapters import get_adapter
from .agents.analyzer import analyze as run_analyze
from .agents.analyzer import summarize_all
from .config import Settings, load_settings
from .db import Blackboard, Status
from .llm.client import LLMClient
from .pipeline import Pipeline, PipelineOptions
from .sandbox import Sandbox
from .scheduler import Scheduler

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="LegacyBridge: verified COBOL -> Python migration with LLM agents.")
console = Console()


@app.callback()
def _root() -> None:
    """LegacyBridge: verified COBOL -> Python migration with LLM agents."""

ADAPTER_OPT = typer.Option("cobol-python", "--adapter", help="Language adapter.")
STATE_OPT = typer.Option(None, "--state", help="State directory (default: pipeline.yaml state_dir).")


def _settings(dry_run: bool, **overrides) -> Settings:
    return load_settings(dry_run=dry_run, **overrides)


def _state_dir(settings: Settings, state: Optional[Path]) -> Path:
    return Path(state) if state else Path(settings.pipeline.state_dir)


def _open_db(settings: Settings, state: Optional[Path], root: Path | None = None, fresh: bool = False) -> Blackboard:
    """Open the blackboard. Refuses to mix two different source trees in one state dir."""
    sdir = _state_dir(settings, state)
    db_path = sdir / "state.db"
    if fresh and db_path.exists():
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
    db = Blackboard(db_path)
    if root is not None:
        prev = db.get_meta("source_root")
        if prev and prev != str(root.resolve()):
            db.close()
            console.print(f"[red]State dir {sdir} belongs to {prev}.[/] Use --fresh or --state <dir>.")
            raise typer.Exit(2)
    return db


def _make_adapter(name: str, root: Path, settings: Settings, state: Optional[Path]):
    sandbox = Sandbox(settings.pipeline.sandbox)
    return get_adapter(name, root, sandbox=sandbox, work_dir=_state_dir(settings, state) / "work")


def _llm(settings: Settings, db: Blackboard, adapter) -> LLMClient:
    return LLMClient(settings, db, mock_brain=adapter.mock_brain() if settings.dry_run else None)


@app.command()
def analyze(
    path: Path = typer.Argument(..., exists=True, file_okay=False, help="Legacy source tree."),
    adapter: str = ADAPTER_OPT,
    state: Optional[Path] = STATE_OPT,
    summaries: bool = typer.Option(False, "--summaries", help="Also ask the LLM for behavior summaries."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Offline: MockProvider instead of real APIs."),
    fresh: bool = typer.Option(False, "--fresh", help="Discard previous state for this state dir."),
    as_json: bool = typer.Option(False, "--json", help="Print the plan as JSON."),
) -> None:
    """Parse the legacy code, build the dependency graph and migration waves."""
    settings = _settings(dry_run)
    db = _open_db(settings, state, path, fresh)
    ad = _make_adapter(adapter, path, settings, state)
    plan, modules = run_analyze(ad, db)
    if summaries:
        asyncio.run(summarize_all(ad, db, _llm(settings, db, ad), settings.pipeline.scheduler.concurrency))
    if as_json:
        console.print_json(json.dumps({"order": plan.order, "cycles": plan.cycles, "external": plan.external,
                                       "units": plan.units, "edges": db.edges()}))
        return

    ok, msg = ad.toolchain_status()
    console.print(("[green]" if ok else "[yellow]") + msg + "[/]")
    t = Table(title=f"Modules in {path}")
    for col in ("module", "kind", "unit", "loc", "calls", "copies", "#feat"):
        t.add_column(col, overflow="fold")
    for m in sorted(modules, key=lambda m: (m.kind == "copybook", m.name)):
        info = m.info
        t.add_row(m.name, m.kind, plan.unit_of[m.name], str(info.get("loc", "")), ", ".join(info.get("calls", [])),
                  ", ".join(info.get("copies", [])), str(len(info.get("features", []))))
    console.print(t)
    if plan.cycles:
        console.print("[bold]Cycles (migrated as one unit):[/] " + "; ".join(" <-> ".join(c) for c in plan.cycles))
    if plan.external:
        console.print("[red]Missing callees:[/] " + ", ".join(plan.external))
    console.print("[bold]Migration waves[/] (dependencies first; units in a wave can run in parallel):")
    for i, wave in enumerate(plan.order):
        console.print(f"  L{i}: " + ", ".join(wave))
    if summaries:
        for row in db.modules():
            if row.get("summary"):
                console.print(f"\n[bold]{row['name']}[/]\n{row['summary']}")
    db.close()


STATUS_STYLE = {"verified": "green", "blocked": "red", "dep_blocked": "red", "pending": "dim"}


async def _tail_events(db: Blackboard, stop: asyncio.Event, quiet: bool) -> None:
    """Print progress from the events table while the scheduler runs."""
    last = db.query("SELECT COALESCE(MAX(id), 0) m FROM events")[0]["m"]
    while True:
        for ev in db.events_after(last):
            last = ev["id"]
            if quiet:
                continue
            p, unit, typ = ev, ev.get("unit") or "", ev["type"]
            if typ == "status" and p.get("status") in ("translating", "repairing", "fuzzing", "verified",
                                                         "blocked", "dep_blocked"):
                style = STATUS_STYLE.get(p["status"], "cyan")
                extra = f" (attempt {p['attempts']})" if p.get("attempts") and p["status"] != "verified" else ""
                console.print(f"[{style}]{unit:<18} {p['status']}{extra}[/]")
            elif typ == "verify":
                mark = "[green]pass[/]" if p.get("ok") else "[yellow]fail[/]"
                console.print(f"{unit:<18} verify {p.get('passed')}/{p.get('total')} {mark} "
                              f"[dim]{p.get('role')} {p.get('model')}[/]")
            elif typ == "oracle":
                console.print(f"[dim]{unit:<18} oracle {p.get('module')}: {p.get('total')} golden tests[/]")
            elif typ == "fuzz_round":
                console.print(f"{unit:<18} fuzz round {p['round']}: {p['new_tests']} new tests, no divergence "
                              f"({p['clean']}/{p['needed']} clean)")
            elif typ == "fuzz_bug":
                console.print(f"[magenta]{unit:<18} fuzz round {p['round']}: {p['counterexamples']} counterexample(s) "
                              f"in {', '.join(p['modules'])} -> repair[/]")
            elif typ == "lesson_added":
                console.print(f"[blue]{unit:<18} lesson #{p['id']}: {p['text'][:110]}[/]")
            elif typ in ("fallback", "unit_crashed", "llm_failed", "fuzz_llm_failed", "reflect_failed"):
                extra = {k: v for k, v in p.items() if k not in ("id", "ts", "type", "unit")}
                console.print(f"[yellow]{unit:<18} {typ}[/] [dim]{json.dumps(extra)[:200]}[/]")
        if stop.is_set():
            return
        await asyncio.sleep(0.3)


def _summary_table(db: Blackboard) -> Table:
    data_only = 0
    t = Table(title="Migration units")
    for col in ("unit", "status", "attempts", "tests", "fuzz", "model", "note"):
        t.add_column(col, overflow="fold")
    for u in db.units():
        members = [m for m in u["members"] if (db.module(m) or {}).get("kind") != "copybook"]
        if not members:
            data_only += 1
            continue
        tests = sum(len(db.test_cases(m, ["llm", "property", "random", "fuzz_llm", "fuzz_mutation"])) for m in members)
        style = STATUS_STYLE.get(u["status"], "cyan")
        t.add_row(u["name"], f"[{style}]{u['status']}[/]", str(u["attempts"] or ""), str(tests or ""),
                  f"{u['fuzz_rounds_clean']} ok / {u['fuzz_bugs_found']} bugs",
                  u.get("model_used") or "", (u.get("error") or "")[:80])
    if data_only:
        t.caption = f"+ {data_only} data-only units (copybooks), nothing to translate"
    return t


@app.command()
def migrate(
    path: Path = typer.Argument(..., exists=True, file_okay=False, help="Legacy source tree."),
    out: Optional[Path] = typer.Option(None, "--out", help="Write the migrated package + report here."),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", "-j", help="Units migrated in parallel."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Offline: MockProvider instead of real APIs."),
    resume: bool = typer.Option(False, "--resume", help="Continue the previous run in this state dir."),
    retry_blocked: bool = typer.Option(False, "--retry-blocked", help="With --resume: retry BLOCKED units."),
    no_fuzz: bool = typer.Option(False, "--no-fuzz", help="Skip adversarial fuzzing rounds."),
    adapter: str = ADAPTER_OPT,
    state: Optional[Path] = STATE_OPT,
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only print the final summary."),
) -> None:
    """Migrate every unit (dependencies first) with differential verification against the original."""
    settings = _settings(dry_run)
    db = _open_db(settings, state, path, fresh=not resume)
    ad = _make_adapter(adapter, path, settings, state)
    plan, _ = run_analyze(ad, db)
    ok, msg = ad.toolchain_status()
    if not ok:
        console.print(f"[red]{msg}[/]\nUnits cannot be verified without it; they will be BLOCKED.")
    llm = _llm(settings, db, ad)
    if not dry_run and not llm.chain("translator"):
        console.print("[red]No API keys found for any configured provider.[/] Add keys to .env (see "
                      ".env.example) or run with --dry-run.")
        raise typer.Exit(2)
    pipe = Pipeline(ad, db, llm, settings, plan, PipelineOptions(fuzz=not no_fuzz))

    async def go() -> dict[str, int]:
        stop = asyncio.Event()
        tail = asyncio.create_task(_tail_events(db, stop, quiet))
        try:
            return await Scheduler(pipe, concurrency).run(retry_blocked=retry_blocked)
        finally:
            stop.set()
            await tail

    counts = asyncio.run(go())
    console.print(_summary_table(db))
    scenarios_ok = True
    if out is not None:
        from .agents.integrator import integrate
        res = asyncio.run(integrate(ad, db, settings, plan, out))
        console.print(f"Package written to [bold]{res.package_dir}[/]; report: [bold]{res.report_path}[/]")
        for sr in res.scenarios:
            mark = {True: "[green]IDENTICAL[/]", False: "[red]DIFFERS[/]", None: "[yellow]skipped[/]"}[sr.passed]
            console.print(f"  scenario {sr.module}/{sr.name}: {mark}" + (f" ({sr.note})" if sr.note else ""))
        scenarios_ok = res.scenarios_ok

    total = sum(counts.values())
    console.print(f"[bold]{counts.get('verified', 0)}/{total} units verified[/]"
                  + (f", {counts.get('blocked', 0)} blocked, {counts.get('dep_blocked', 0)} dep-blocked"
                     if counts.get("verified", 0) < total else ""))
    db.close()
    if counts.get("verified", 0) < total or not scenarios_ok:
        raise typer.Exit(1)


@app.command()
def verify(
    path: Path = typer.Argument(..., exists=True, file_okay=False, help="Legacy source tree."),
    adapter: str = ADAPTER_OPT,
    state: Optional[Path] = STATE_OPT,
    include_hidden: bool = typer.Option(False, "--hidden", help="Also run the held-out eval suite."),
) -> None:
    """Re-run every stored translation against all recorded golden tests (deterministic verdicts)."""
    from .agents.verifier import verify_module

    settings = _settings(True)
    db = _open_db(settings, state, path)
    ad = _make_adapter(adapter, path, settings, state)
    ad.load(ad.discover())
    code = {ad.target_name(m["name"]): m["python_code"] for m in db.modules() if m.get("python_code")}
    if not code:
        console.print("[yellow]No migrated modules in this state dir; run `legacybridge migrate` first.[/]")
        raise typer.Exit(1)
    origins = ["llm", "property", "random", "fuzz_llm", "fuzz_mutation"] + (["hidden"] if include_hidden else [])
    t = Table(title="Verification")
    for col in ("module", "tests", "passed", "verdict"):
        t.add_column(col)

    async def go() -> bool:
        all_ok = True
        for m in db.modules():
            if not m.get("python_code"):
                continue
            rep = await verify_module(ad, m["name"], code, db.test_cases(m["name"], origins),
                                      settings.pipeline.verifier.normalize)
            all_ok &= rep.ok
            t.add_row(m["name"], str(rep.total), str(rep.passed),
                      "[green]PASS[/]" if rep.ok else f"[red]FAIL[/] {rep.static_error or ''}")
        return all_ok

    ok = asyncio.run(go())
    console.print(t)
    db.close()
    if not ok:
        raise typer.Exit(1)


@app.command()
def report(
    path: Path = typer.Argument(..., exists=True, file_okay=False, help="Legacy source tree."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output dir (default: <state>/report)."),
    rerun: bool = typer.Option(True, "--scenarios/--no-scenarios",
                               help="Re-package and replay end-to-end scenarios (else reuse the last results)."),
    adapter: str = ADAPTER_OPT,
    state: Optional[Path] = STATE_OPT,
) -> None:
    """Write REPORT.md + report.json for the run in this state dir."""
    from .agents.integrator import build_report, integrate, stored_scenarios, write_report

    settings = _settings(True)
    db = _open_db(settings, state, path)
    ad = _make_adapter(adapter, path, settings, state)
    plan, _ = run_analyze(ad, db)
    out = out or _state_dir(settings, state) / "report"
    if rerun:
        res = asyncio.run(integrate(ad, db, settings, plan, out))
        md, scen = res.report_path, res.scenarios
    else:
        pkg = db.get_meta("package_dir")
        scen = stored_scenarios(db)
        md, _ = write_report(build_report(ad, db, plan, scen, Path(pkg) if pkg else None), out)
    db.close()
    bad = [s for s in scen if s.passed is False]
    console.print(f"Report: [bold]{md}[/]  scenarios: {sum(s.passed is True for s in scen)}/{len(scen)} identical"
                  + (f", [red]{len(bad)} differ[/]" if bad else ""))
    if bad:
        raise typer.Exit(1)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
