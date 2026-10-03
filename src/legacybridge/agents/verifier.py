"""Deterministic verifier: runs a translation on every golden test case and diffs the result.

No LLM is involved. Default comparison is EXACT (stdout bytes + exit code); the only allowed
normalizations are the ones switched on in config/pipeline.yaml (verifier.normalize).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..adapters.base import LanguageAdapter
from ..config import NormalizeCfg
from ..sandbox import RunResult


@dataclass
class CaseResult:
    test_id: int
    passed: bool
    actual_stdout: str
    actual_exit: int
    diff: dict[str, Any] | None = None


@dataclass
class VerifyReport:
    module: str
    total: int = 0
    passed: int = 0
    failures: list[CaseResult] = field(default_factory=list)
    results: list[CaseResult] = field(default_factory=list)
    static_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.static_error is None and self.total > 0 and self.passed == self.total

    def summary(self) -> str:
        if self.static_error:
            return f"{self.module}: static check failed: {self.static_error}"
        return f"{self.module}: {self.passed}/{self.total} tests pass"


def normalize(text: str, cfg: NormalizeCfg) -> list[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if cfg.rstrip_lines:
        lines = [ln.rstrip() for ln in lines]
    if cfg.ignore_trailing_blank_lines:
        while lines and not lines[-1].strip():
            lines.pop()
    return lines


def diff_case(expected_stdout: str, expected_exit: int, actual: RunResult, cfg: NormalizeCfg) -> dict[str, Any] | None:
    """Structured description of the first difference, or None if the outputs match."""
    if actual.timed_out:
        return {"kind": "timeout", "stderr_tail": actual.stderr[-800:]}
    exp, got = normalize(expected_stdout, cfg), normalize(actual.stdout, cfg)
    out: dict[str, Any] = {}
    if exp != got:
        i = next((k for k in range(min(len(exp), len(got))) if exp[k] != got[k]), min(len(exp), len(got)))
        out.update({
            "kind": "stdout", "line": i + 1,
            "expected": exp[i] if i < len(exp) else "<no more lines>",
            "actual": got[i] if i < len(got) else "<no more lines>",
            "expected_lines": len(exp), "actual_lines": len(got),
            "context_expected": exp[max(0, i - 2): i + 3], "context_actual": got[max(0, i - 2): i + 3],
        })
    if expected_exit != actual.exit_code:
        out.setdefault("kind", "exit")
        out["expected_exit"], out["actual_exit"] = expected_exit, actual.exit_code
    if out and actual.stderr.strip():
        out["stderr_tail"] = actual.stderr[-1200:]
    if actual.truncated:
        out.setdefault("kind", "stdout")
        out["note"] = "output truncated (runaway output?)"
    return out or None


async def verify_module(adapter: LanguageAdapter, module: str, code: dict[str, str], tests: list[dict[str, Any]],
                        cfg: NormalizeCfg, *, fail_fast: int | None = None) -> VerifyReport:
    """Run `module`'s translation (with deps in `code`) on all `tests`."""
    rep = VerifyReport(module, total=len(tests))
    target = adapter.target_name(module)
    if target not in code:
        rep.static_error = f"no code for module {target}"
        return rep
    if err := adapter.check_target(module, code[target]):
        rep.static_error = err
        return rep

    async def one(tc: dict[str, Any]) -> CaseResult:
        res = await adapter.run_target(module, code, tc["input"])
        d = diff_case(tc["expected_stdout"], tc["expected_exit"], res, cfg)
        if d is not None and d.get("kind") == "stdout":
            detail = adapter.describe_mismatch(module, tc["input"], tc["expected_stdout"], res.stdout)
            if detail:
                d["fields"] = detail
        return CaseResult(tc["id"], d is None, res.stdout, res.exit_code, d)

    results = await asyncio.gather(*(one(tc) for tc in tests))
    rep.results = list(results)
    rep.failures = [r for r in results if not r.passed]
    rep.passed = rep.total - len(rep.failures)
    if fail_fast:
        rep.failures = rep.failures[:fail_fast]
    return rep


def failure_digest(rep: VerifyReport, tests_by_id: dict[int, dict[str, Any]], limit: int = 4,
                   max_input_chars: int = 900) -> str:
    """Human/LLM-readable digest of failures for a repair prompt."""
    if rep.static_error:
        return f"Module {rep.module}: static check failed: {rep.static_error}"
    parts = [f"Module {rep.module}: {rep.passed}/{rep.total} tests pass. First failures:"]
    for r in rep.failures[:limit]:
        tc = tests_by_id.get(r.test_id, {})
        d = r.diff or {}
        inp = tc.get("input", "")
        if len(inp) > max_input_chars:
            inp = inp[:max_input_chars] + "...(truncated)"
        block = [f"--- test #{r.test_id} ({tc.get('origin', '?')}) input:\n{inp.rstrip()}"]
        if d.get("kind") == "timeout":
            block.append("TIMEOUT (infinite loop?)")
        if "line" in d:
            block.append(f"first difference at output line {d['line']} "
                         f"(expected {d['expected_lines']} lines, got {d['actual_lines']}):")
            block.append(f"  expected: {d['expected']!r}\n  actual:   {d['actual']!r}")
        if "fields" in d:
            block.append("field differences:\n" + d["fields"])
        if "expected_exit" in d:
            block.append(f"exit code: expected {d['expected_exit']}, got {d['actual_exit']}")
        if d.get("stderr_tail"):
            block.append("stderr:\n" + d["stderr_tail"][-700:])
        parts.append("\n".join(block))
    return "\n".join(parts)
