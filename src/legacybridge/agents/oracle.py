"""Oracle builder: produce characterization tests for a module and record the ORIGINAL program's
behavior on each (stdout + exit code) as the golden result.

Inputs come from three sources: LLM-proposed scenarios (semantic coverage), property/boundary
inputs and seeded random inputs (both deterministic, from the data definitions). The LLM only
proposes inputs; expected outputs always come from running the legacy code.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..adapters.base import GeneratedInput, LanguageAdapter
from ..config import Settings
from ..db import Blackboard
from ..llm.prompt import Prompt

ORACLE_SYSTEM = (
    "You design characterization tests for legacy {lang} code that is about to be migrated. The tests "
    "will be run against the original program to capture its exact behavior, so aim for inputs that "
    "exercise every branch and every tricky semantic, not for 'correct' business outcomes."
)
ORACLE_INSTRUCTION = (
    "Propose {n} diverse test cases for {name}. Cover: the normal path; every error/return code; "
    "boundaries (zero, maximum PIC values, negative numbers, values that overflow or truncate on "
    "store, rounding at .5); invalid codes; dates (leap years, month ends, invalid dates); and, when "
    "relevant, sequences where earlier records/CALLs change later behavior. Give each case a short "
    "name and a one-line 'why'."
)


class OracleError(RuntimeError):
    """The module cannot get golden tests (untestable, compile error, no usable inputs)."""


class LLMCase(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = "case"
    why: str = ""


class LLMCases(BaseModel):
    cases: list[LLMCase] = Field(default_factory=list)


def module_rng(seed: int, module: str, salt: str = "") -> random.Random:
    """Deterministic RNG per (seed, module, purpose) independent of scheduling order."""
    return random.Random(f"{seed}:{module}:{salt}")


async def llm_inputs(adapter: LanguageAdapter, llm, module: str, n: int, *, unit: str | None,
                     summary: str | None, extra: str = "", role: str = "oracle",
                     task: str = "oracle", meta: dict[str, Any] | None = None) -> tuple[list[GeneratedInput], list[str]]:
    """Ask the LLM for test cases; returns (valid inputs, rejection reasons)."""
    p = Prompt(ORACLE_SYSTEM.format(lang=adapter.source_language),
               instruction=ORACLE_INSTRUCTION.format(n=n, name=module) + (("\n" + extra) if extra else ""))
    p.add("Input format", adapter.input_spec(module) + "\n\n" + adapter.llm_case_instructions(module),
          priority=90, required=True)
    p.add("Behavior summary", summary or "", priority=40)
    p.add("Tricky semantics present", ", ".join(adapter.features(module)), priority=50)
    ctx = adapter.module_context(module)
    p.add("Source", ctx, priority=60, compact=ctx[:6000] + "\n...(truncated)")
    res = await llm.complete(role, p, LLMCases, meta={"task": task, "module": module, "n": n, **(meta or {})}, unit=unit)
    out: list[GeneratedInput] = []
    rejected: list[str] = []
    for i, case in enumerate(res.parsed.cases[: n * 2], 1):
        try:
            text = adapter.canonical_input(module, adapter.parse_llm_case(module, case.model_dump()))
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append(f"{case.name}: {exc}")
            continue
        if reason := adapter.validate_input(module, text):
            rejected.append(f"{case.name}: {reason}")
            continue
        origin = "llm" if task == "oracle" else "fuzz_llm"
        out.append(GeneratedInput(f"{origin}-{i}-{case.name}"[:80], text, origin))
    return out, rejected


async def record_golden(adapter: LanguageAdapter, db: Blackboard, module: str, inputs: list[GeneratedInput],
                        *, unit: str | None) -> list[dict[str, Any]]:
    """Run the legacy module on each new input and store the golden results. Returns new rows."""
    fresh, seen = [], set()
    for gi in inputs:
        text = adapter.canonical_input(module, gi.text)
        if text in seen or db.has_input(module, text) or adapter.validate_input(module, text):
            continue
        seen.add(text)
        fresh.append((gi, text))
    results = await asyncio.gather(*(adapter.run_legacy(module, text) for _, text in fresh))
    rows = []
    for (gi, text), res in zip(fresh, results):
        if res.timed_out or res.truncated:
            db.emit("oracle_input_dropped", unit, module=module, name=gi.name,
                    reason="timeout" if res.timed_out else "output too large")
            continue
        tid = db.add_test_case(module, gi.origin, text, res.stdout, res.exit_code, name=gi.name)
        if tid is not None:
            rows.append({"id": tid, "module": module, "origin": gi.origin, "name": gi.name, "input": text,
                         "expected_stdout": res.stdout, "expected_exit": res.exit_code})
    return rows


async def build_oracle(adapter: LanguageAdapter, db: Blackboard, llm, settings: Settings, module: str,
                       *, unit: str | None = None) -> int:
    """Create golden test cases for `module`. Idempotent (existing inputs are skipped)."""
    cfg = settings.pipeline.oracle
    if reason := adapter.testable(module):
        raise OracleError(f"{module} is not differentially testable: {reason}")
    ok, msg = adapter.toolchain_status()
    if not ok:
        raise OracleError(msg)
    try:
        await adapter.build_legacy(module)          # type: ignore[attr-defined]
    except Exception as exc:
        raise OracleError(f"cannot build original {module}: {exc}") from exc

    # determinism probe: identical runs must give identical results, otherwise nothing is verifiable
    probe = adapter.property_inputs(module, 1, module_rng(cfg.seed, module, "probe"))
    if probe:
        a, b = await asyncio.gather(*(adapter.run_legacy(module, probe[0].text) for _ in range(2)))
        if (a.stdout, a.exit_code) != (b.stdout, b.exit_code):
            raise OracleError(f"{module}: original program is non-deterministic on identical input")

    inputs: list[GeneratedInput] = []
    if cfg.llm_cases > 0:
        row = db.module(module) or {}
        try:
            got, rejected = await llm_inputs(adapter, llm, module, cfg.llm_cases, unit=unit,
                                             summary=row.get("summary"))
            inputs += got
            if rejected:
                db.emit("oracle_llm_rejected", unit, module=module, count=len(rejected), examples=rejected[:5])
        except Exception as exc:  # LLM inputs are a bonus; deterministic generators still run
            db.emit("oracle_llm_failed", unit, module=module, error=str(exc)[:300])
    inputs += adapter.property_inputs(module, cfg.property_cases, module_rng(cfg.seed, module, "property"))
    inputs += adapter.random_inputs(module, cfg.random_cases, module_rng(cfg.seed, module, "random"))
    rows = await record_golden(adapter, db, module, inputs, unit=unit)
    total = len(db.test_cases(module, origins=["llm", "property", "random"]))
    by_origin: dict[str, int] = {}
    for r in rows:
        by_origin[r["origin"]] = by_origin.get(r["origin"], 0) + 1
    db.emit("oracle", unit, module=module, added=len(rows), total=total, by_origin=by_origin)
    if total == 0:
        raise OracleError(f"{module}: no usable test inputs")
    return total


async def build_unit_oracle(adapter: LanguageAdapter, db: Blackboard, llm, settings: Settings, unit: str,
                            members: list[str]) -> int:
    """Oracle for every executable member of a migration unit; marks the unit oracle_done."""
    total = 0
    for m in members:
        if (db.module(m) or {}).get("kind") != "copybook":
            total += await build_oracle(adapter, db, llm, settings, m, unit=unit)
    db.update_unit(unit, oracle_done=1)
    return total


async def build_hidden_suite(adapter: LanguageAdapter, db: Blackboard, module: str, n: int, seed: int) -> int:
    """Held-out tests (never shown to translator/verifier) used by `eval` to measure residual bugs."""
    rng = module_rng(seed, module, "hidden")
    inputs = adapter.property_inputs(module, n // 2, rng) + adapter.random_inputs(module, n - n // 2, rng)
    for gi in inputs:
        gi.origin = "hidden"
    rows = await record_golden(adapter, db, module, inputs, unit=None)
    return len(rows)
