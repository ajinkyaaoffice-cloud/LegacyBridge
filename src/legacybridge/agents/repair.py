"""Repair loop: translate -> verify -> (repair -> verify)* with model escalation.

Attempt 1 is the translation. Each failed attempt feeds a deterministic failure digest (inputs,
first differing line, per-field diffs, exit codes, stderr) back to the model. After
`repair.escalate_after` failed attempts the 'repair_escalated' role (stronger models) takes over.
The verdict is always the verifier's, never the model's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapters.base import LanguageAdapter
from ..config import Settings
from ..db import Blackboard, Status
from ..llm.types import LLMError
from .translator import base_prompt, collect_code, translate_unit
from .verifier import VerifyReport, failure_digest, verify_module

REPAIR_SYSTEM = (
    "You fix {dst} translations of legacy {src} programs. The translation must reproduce the "
    "original program's output byte for byte; the failing differential tests below were recorded "
    "from the original. Find the semantic mismatch (truncation, rounding, padding, persistence of "
    "WORKING-STORAGE, record layout, edited pictures...) and fix it without breaking passing cases. "
    "Reply with code only."
)
REPAIR_INSTRUCTION = (
    "Return the COMPLETE corrected module(s) {targets}, one ```python fenced block each, first line "
    "`# module: <module name>`. Modules you don't change may be omitted."
)


@dataclass
class LoopResult:
    """Outcome of translate+repair for one unit."""

    ok: bool
    code: dict[str, str]                       # {target module: source} of the last attempt
    attempts: int
    reports: dict[str, VerifyReport] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)   # per attempt: kind, role, model, passed/total
    model: str | None = None
    error: str | None = None

    @property
    def repaired(self) -> bool:
        """True when the unit passed only after at least one repair."""
        return self.ok and self.attempts > 1


async def verify_unit(adapter: LanguageAdapter, members: list[str], code: dict[str, str],
                      dep_code: dict[str, str], tests: dict[str, list[dict[str, Any]]],
                      settings: Settings) -> dict[str, VerifyReport]:
    """Verify every member against its tests, with already-verified dependencies available."""
    full = {**dep_code, **code}
    return {m: await verify_module(adapter, m, full, tests[m], settings.pipeline.verifier.normalize)
            for m in members}


def digest(reports: dict[str, VerifyReport], tests: dict[str, list[dict[str, Any]]], limit: int = 4) -> str:
    """Failure digest across all failing members."""
    parts = []
    for m, rep in reports.items():
        if not rep.ok:
            parts.append(failure_digest(rep, {t["id"]: t for t in tests[m]}, limit=limit))
    return "\n\n".join(parts)


def _record(db: Blackboard, unit: str, attempt_id: int, reports: dict[str, VerifyReport], stage: str) -> None:
    ok = all(r.ok for r in reports.values())
    summary = {m: {"passed": r.passed, "total": r.total, "static_error": r.static_error,
                   "first_failure": (r.failures[0].diff if r.failures else None)} for m, r in reports.items()}
    db.finish_attempt(attempt_id, "pass" if ok else ("error" if any(r.static_error for r in reports.values())
                                                     else "fail"), summary, stage)
    rows = [{"module": m, "test_case_id": c.test_id, "passed": c.passed,
             "actual_stdout": None if c.passed else c.actual_stdout, "diff": c.diff}
            for m, r in reports.items() for c in r.results]
    if rows:
        db.add_verification_results(attempt_id, rows)


async def repair_unit(adapter: LanguageAdapter, db: Blackboard, llm, unit: str, members: list[str],
                      code: dict[str, str], failures: str, *, dep_code: dict[str, str],
                      lessons: list[dict[str, Any]], attempt: int, role: str):
    """One repair call. Returns ({target: code}, llm result); unchanged modules keep `code`."""
    targets = [adapter.target_name(m) for m in members]
    p = base_prompt(adapter, db, members, dep_code, lessons,
                    REPAIR_SYSTEM.format(src=adapter.source_language, dst=adapter.target_language),
                    REPAIR_INSTRUCTION.format(targets=", ".join(targets)))
    p.add("Failing differential tests", failures, priority=97, required=True,
          compact=failures[:3000] + ("\n...(truncated)" if len(failures) > 3000 else ""))
    cur = "\n\n".join(f"```python\n{code[t]}\n```" for t in targets if t in code)
    p.add("Current translation", cur or "(no code was produced)", priority=94, required=True)
    for m in members:
        p.add(f"Source of {m}", adapter.module_context(m), priority=93, required=True)
    res = await llm.complete(role, p, None, unit=unit,
                             meta={"task": "repair", "unit": unit, "modules": members, "attempt": attempt})
    return collect_code(adapter, members, res.text, previous=code), res


async def translate_and_repair(adapter: LanguageAdapter, db: Blackboard, llm, settings: Settings, unit: str,
                               members: list[str], *, dep_code: dict[str, str],
                               tests: dict[str, list[dict[str, Any]]], lessons: list[dict[str, Any]] | None = None,
                               max_attempts: int | None = None, repair: bool = True,
                               start_code: dict[str, str] | None = None, start_failures: str | None = None,
                               attempt_offset: int = 0) -> LoopResult:
    """Run the loop for executable `members` until all tests pass or attempts run out.

    `start_code`/`start_failures` resume from an existing failing translation (used by the fuzzer
    when a new counterexample appears); then every attempt is a repair.
    """
    cfg = settings.pipeline.repair
    max_attempts = max_attempts or cfg.max_attempts
    lessons = lessons or []
    lesson_ids = [les["id"] for les in lessons if "id" in les]
    code: dict[str, str] = dict(start_code or {})
    failures = start_failures
    result = LoopResult(ok=False, code=code, attempts=0)
    failed = attempt_offset
    for n in range(1, max_attempts + 1):
        no = attempt_offset + n
        is_translate = start_code is None and n == 1
        if not is_translate and not repair:
            break
        role = "translator" if is_translate else ("repair_escalated" if failed >= cfg.escalate_after else "repair")
        db.set_status(unit, Status.TRANSLATING if is_translate else Status.REPAIRING, attempts=no)
        try:
            if is_translate:
                code, res = await translate_unit(adapter, db, llm, unit, members, dep_code=dep_code,
                                                 lessons=lessons, attempt=no, role=role)
            else:
                code, res = await repair_unit(adapter, db, llm, unit, members, code, failures or "", dep_code=dep_code,
                                              lessons=lessons, attempt=no, role=role)
        except LLMError as exc:
            result.error = f"LLM unavailable: {exc}"
            db.emit("llm_failed", unit, attempt=no, role=role, error=str(exc)[:400])
            break
        result.model = res.model_ref
        attempt_id = db.add_attempt(unit, no, "translate" if is_translate else "repair", res.model_ref, code,
                                    lesson_ids)
        db.set_status(unit, Status.VERIFYING, attempts=no, model_used=res.model_ref)
        reports = await verify_unit(adapter, members, code, dep_code, tests, settings)
        _record(db, unit, attempt_id, reports, "fuzz" if start_code is not None else "oracle")
        ok = all(r.ok for r in reports.values())
        entry = {"attempt": no, "kind": "translate" if is_translate else "repair", "role": role,
                 "model": res.model_ref, "passed": sum(r.passed for r in reports.values()),
                 "total": sum(r.total for r in reports.values()),
                 "static_error": next((r.static_error for r in reports.values() if r.static_error), None)}
        result.history.append(entry)
        db.emit("verify", unit, **entry, ok=ok)
        result.code, result.reports, result.attempts = code, reports, n
        if ok:
            result.ok = True
            break
        failed += 1
        failures = digest(reports, tests)
    if not result.ok and result.error is None:
        result.error = digest(result.reports, tests, limit=1)[:1500] or "no passing translation"
    return result
