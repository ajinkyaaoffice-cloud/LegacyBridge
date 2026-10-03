"""Adversarial fuzzer: after a unit passes its oracle tests, hunt for inputs where the translation
still diverges from the original, until K consecutive rounds find nothing.

Each round asks the LLM for inputs aimed at the CURRENT translation's weak spots and adds
deterministic mutations of existing inputs (literal boundaries, sign flips, +-1 ulp, line
duplication/reordering). New inputs are run against the original program (golden), stored as
tests, and the translation is verified on them. A counterexample triggers the repair loop against
ALL tests; the clean-round counter then restarts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..adapters.base import GeneratedInput
from ..db import Status
from .oracle import llm_inputs, module_rng, record_golden
from .repair import LoopResult, digest, translate_and_repair
from .verifier import verify_module

if TYPE_CHECKING:
    from ..pipeline import Pipeline

FUZZ_EXTRA = (
    "A Python translation of this program already passes all existing tests. Your goal is to BREAK it: "
    "propose inputs where the translation below most likely behaves differently from the COBOL original "
    "(truncation vs rounding, overflow of intermediate fields, sign handling, padding, invalid numeric "
    "text, state carried between calls/records, unusual codes, extreme dates).\n"
    "Current translation:\n```python\n{code}\n```"
)


async def fuzz_round_inputs(pipe: "Pipeline", unit: str, module: str, code: str, round_no: int) -> list[GeneratedInput]:
    """LLM-proposed + mutation inputs for one module and round."""
    cfg = pipe.settings.pipeline
    inputs: list[GeneratedInput] = []
    if cfg.fuzzer.llm_inputs_per_round > 0:
        try:
            got, rejected = await llm_inputs(
                pipe.adapter, pipe.llm, module, cfg.fuzzer.llm_inputs_per_round, unit=unit,
                summary=(pipe.db.module(module) or {}).get("summary"),
                extra=FUZZ_EXTRA.format(code=code[:12000]), role="fuzzer", task="fuzz", meta={"round": round_no})
            for gi in got:
                gi.name = f"r{round_no}-{gi.name}"[:80]
            inputs += got
        except Exception as exc:  # noqa: BLE001 - LLM inputs are a bonus; mutations still run
            pipe.db.emit("fuzz_llm_failed", unit, module=module, error=str(exc)[:300])
    seeds = [t["input"] for t in pipe.db.test_cases(module) if t["origin"] != "hidden"]
    rng = module_rng(cfg.oracle.seed, module, f"fuzz-{round_no}")
    for gi in pipe.adapter.mutation_inputs(module, seeds, cfg.fuzzer.mutation_inputs_per_round, rng):
        gi.name = f"r{round_no}-{gi.name}"[:80]
        inputs.append(gi)
    return inputs


async def fuzz_unit(pipe: "Pipeline", unit: str, members: list[str], loop: LoopResult, dep_code: dict[str, str],
                    lessons: list[dict[str, Any]]) -> LoopResult:
    """Run fuzz rounds until `fuzzer.rounds` consecutive clean rounds (or a repair fails)."""
    cfg = pipe.settings.pipeline
    need = cfg.fuzzer.rounds
    max_rounds = need + 2 * cfg.repair.max_attempts       # bound total work if bugs keep appearing
    db, adapter = pipe.db, pipe.adapter
    u = db.unit(unit) or {}
    clean, bugs = 0, int(u.get("fuzz_bugs_found") or 0)
    attempts_used = int(u.get("attempts") or loop.attempts)
    code = loop.code
    for round_no in range(1, max_rounds + 1):
        if clean >= need:
            break
        new_tests: dict[str, list[dict[str, Any]]] = {}
        for m in members:
            target = adapter.target_name(m)
            inputs = await fuzz_round_inputs(pipe, unit, m, code.get(target, ""), round_no)
            new_tests[m] = await record_golden(adapter, db, m, inputs, unit=unit)
        failing = {}
        full = {**dep_code, **code}
        for m in members:
            if not new_tests[m]:
                continue
            rep = await verify_module(adapter, m, full, new_tests[m], cfg.verifier.normalize)
            if not rep.ok:
                failing[m] = rep
        added = sum(len(v) for v in new_tests.values())
        if not failing:
            clean += 1
            db.update_unit(unit, fuzz_rounds_clean=clean)
            db.emit("fuzz_round", unit, round=round_no, new_tests=added, clean=clean, needed=need)
            continue
        found = sum(len(r.failures) for r in failing.values())
        bugs += found
        clean = 0
        db.update_unit(unit, fuzz_rounds_clean=0, fuzz_bugs_found=bugs)
        tests_by_module = {m: new_tests[m] for m in failing}
        db.emit("fuzz_bug", unit, round=round_no, new_tests=added, counterexamples=found,
                modules=sorted(failing), digest=digest(failing, tests_by_module, limit=1)[:600])
        all_tests = pipe.tests(members)
        rep_loop = await translate_and_repair(   # a fresh repair budget per counterexample
            adapter, db, pipe.llm, pipe.settings, unit, members, dep_code=dep_code, tests=all_tests,
            lessons=lessons, max_attempts=cfg.repair.max_attempts, repair=pipe.opts.repair, start_code=code,
            start_failures=digest(failing, tests_by_module), attempt_offset=attempts_used)
        attempts_used += rep_loop.attempts
        rep_loop.history = loop.history + rep_loop.history
        rep_loop.attempts = attempts_used
        loop = rep_loop
        if not loop.ok:
            loop.error = "fuzzer found a counterexample that could not be repaired: " + (loop.error or "")
            return loop
        code = loop.code
        db.set_status(unit, Status.FUZZING)
    if clean < need:
        loop.ok = False
        loop.error = f"fuzzing did not reach {need} clean rounds within {max_rounds} rounds"
    return loop
