"""Analyzer agent: discover modules, build the dependency/SCC plan, load it onto the blackboard,
and (optionally) ask an LLM for a behavior summary of each executable module.

The plan itself is fully deterministic; LLM summaries are context for later prompts and the
dashboard only, never inputs to any verdict.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..adapters.base import LanguageAdapter, SourceModule
from ..db import Blackboard, Status
from ..graph import MigrationPlan, build_plan
from ..llm.prompt import Prompt, chunk_text, estimate_tokens

SUMMARY_SYSTEM = (
    "You are a senior mainframe engineer documenting legacy {lang} code before a migration. "
    "Be precise and concrete; never speculate beyond the code."
)
SUMMARY_INSTRUCTION = (
    "Summarize the behavior of module {name} in at most 12 bullet points: purpose, inputs "
    "(records / parameters), outputs, return codes, and tricky semantics (rounding, truncation, "
    "sign handling, overflow, date math, state kept between calls). Plain text, no code."
)
CHUNK_TOKENS = 4000   # fits the smallest free-tier TPM windows with room for the answer


def analyze(adapter: LanguageAdapter, db: Blackboard) -> tuple[MigrationPlan, list[SourceModule]]:
    """Discover + plan + persist. Idempotent: re-running keeps unit statuses (resume)."""
    modules = adapter.discover()
    by_name = {m.name: m for m in modules}
    edges = [(m.name, dep, kind) for m in modules for dep, kind in m.deps]
    plan = build_plan({m.name: {"kind": m.kind, "executable": m.executable} for m in modules}, edges)

    for m in modules:
        db.upsert_module(m.name, m.kind, str(m.path), plan.unit_of[m.name], m.source, m.info)
    for src, dst, kind in edges:
        db.add_edge(src, dst, kind)
    for uname, members in plan.units.items():
        if all(n in by_name for n in members):
            data_only = not any(by_name[n].executable for n in members)
            db.upsert_unit(uname, members)
            if data_only and db.unit(uname)["status"] != Status.VERIFIED:
                db.set_status(uname, Status.VERIFIED, error="data-only (copybook): nothing to translate")
    for ext in plan.external:
        for dependent in plan.dependents_of(plan.unit_of[ext]):
            db.set_status(dependent, Status.DEP_BLOCKED, error=f"calls {ext}, which is not in the project")
    db.set_meta("plan", {"order": plan.order, "cycles": plan.cycles, "external": plan.external,
                         "units": plan.units})
    db.set_meta("source_root", str(Path(adapter.root).resolve()))
    db.emit("analyzed", None, modules=len(modules), units=len(plan.units), cycles=plan.cycles,
            waves=plan.order)
    return plan, modules


async def summarize_module(adapter: LanguageAdapter, llm, name: str, unit: str | None = None) -> str:
    """LLM behavior summary; large modules are summarized chunk-by-chunk, then merged."""
    context = adapter.module_context(name)
    system = SUMMARY_SYSTEM.format(lang=adapter.source_language)
    instruction = SUMMARY_INSTRUCTION.format(name=name)
    meta = {"task": "summarize", "module": name}
    if estimate_tokens(context) <= CHUNK_TOKENS:
        p = Prompt(system, instruction=instruction).add("Source", context, required=True)
        return (await llm.complete("summarizer", p, meta=meta, unit=unit)).text.strip()
    partials = []
    chunks = chunk_text(context, CHUNK_TOKENS)
    for i, chunk in enumerate(chunks, 1):
        p = Prompt(system, instruction=f"Summarize what this part ({i}/{len(chunks)}) of {name} does, "
                                       "in at most 8 bullets.").add("Source part", chunk, required=True)
        partials.append((await llm.complete("summarizer", p, meta={**meta, "chunk": i}, unit=unit)).text.strip())
    p = Prompt(system, instruction=instruction).add("Partial summaries", "\n\n".join(partials), required=True)
    return (await llm.complete("summarizer", p, meta={**meta, "chunk": "merge"}, unit=unit)).text.strip()


async def summarize_all(adapter: LanguageAdapter, db: Blackboard, llm, concurrency: int = 4,
                        force: bool = False) -> dict[str, str]:
    """Summarize every executable module lacking a summary. Failures are recorded, not fatal."""
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, str] = {}
    rows = [r for r in db.modules() if r["kind"] != "copybook" and (force or not r.get("summary"))]

    async def one(row: dict) -> None:
        async with sem:
            try:
                text = await summarize_module(adapter, llm, row["name"], unit=row["unit"])
            except Exception as exc:  # summaries are optional context
                db.emit("summary_failed", row["unit"], module=row["name"], error=str(exc)[:300])
                return
            db.set_module_fields(row["name"], summary=text)
            db.emit("summary", row["unit"], module=row["name"])
            out[row["name"]] = text

    await asyncio.gather(*(one(r) for r in rows))
    return out
