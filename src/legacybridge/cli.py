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
from .db import Blackboard
from .llm.client import LLMClient
from .sandbox import Sandbox

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


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
