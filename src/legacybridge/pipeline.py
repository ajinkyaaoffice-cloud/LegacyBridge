"""Per-unit migration pipeline: oracle -> translate -> verify -> repair -> fuzz -> VERIFIED.

The pipeline only reads and writes the blackboard; the scheduler decides WHEN a unit runs.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import dataclass
from typing import Any

from .adapters.base import LanguageAdapter
from .agents.oracle import OracleError, build_unit_oracle
from .agents.repair import LoopResult, translate_and_repair
from .agents.translator import executable_members
from .config import Settings
from .db import Blackboard, Status
from .graph import MigrationPlan

ORACLE_ORIGINS = ["llm", "property", "random"]
FUZZ_ORIGINS = ["fuzz_llm", "fuzz_mutation"]


@dataclass
class PipelineOptions:
    """Feature switches (the eval baselines turn parts of the pipeline off)."""

    repair: bool = True              # feed failures back and retry
    max_attempts: int | None = None  # default: pipeline.yaml repair.max_attempts
    fuzz: bool = True                # adversarial rounds before VERIFIED
    lessons: bool = True             # reflector lessons in prompts
    gate: bool = True                # False: accept the last translation even if tests fail (baseline A)


class Pipeline:
    """Everything needed to migrate one unit, bound to one run."""

    def __init__(self, adapter: LanguageAdapter, db: Blackboard, llm, settings: Settings, plan: MigrationPlan,
                 options: PipelineOptions | None = None):
        self.adapter = adapter
        self.db = db
        self.llm = llm
        self.settings = settings
        self.plan = plan
        self.opts = options or PipelineOptions()

    # ------------------------------------------------------------ helpers
    def members(self, unit: str) -> list[str]:
        return executable_members(self.db, self.plan.units[unit])

    def verified_code(self) -> dict[str, str]:
        """{target module: code} of every module that has an accepted translation."""
        out = {}
        for m in self.db.modules():
            if m.get("python_code"):
                out[self.adapter.target_name(m["name"])] = m["python_code"]
        return out

    def tests(self, members: list[str], with_fuzz: bool = True) -> dict[str, list[dict[str, Any]]]:
        origins = ORACLE_ORIGINS + (FUZZ_ORIGINS if with_fuzz else [])
        return {m: self.db.test_cases(m, origins) for m in members}

    def lessons_for(self, members: list[str]) -> list[dict[str, Any]]:
        """Lessons relevant to these members (filled in by the reflector, Phase 6)."""
        return []

    # ------------------------------------------------------------ stages
    async def ensure_oracle(self, unit: str) -> str | None:
        """Build golden tests for the unit if not done. Returns an error message or None."""
        u = self.db.unit(unit) or {}
        if u.get("oracle_done"):
            return None
        try:
            await build_unit_oracle(self.adapter, self.db, self.llm, self.settings, unit, self.plan.units[unit])
        except OracleError as exc:
            return str(exc)
        return None

    async def process_unit(self, unit: str) -> Status:
        """Translate, verify, repair and fuzz one unit whose dependencies are done. Returns final status."""
        members = self.members(unit)
        started = time.time()
        self.db.update_unit(unit, started_at=started, error=None)
        lessons = self.lessons_for(members) if self.opts.lessons else []
        dep_code = self.verified_code()
        loop = await translate_and_repair(
            self.adapter, self.db, self.llm, self.settings, unit, members, dep_code=dep_code,
            tests=self.tests(members), lessons=lessons, max_attempts=self.opts.max_attempts,
            repair=self.opts.repair)
        if loop.ok and self.opts.fuzz and self.settings.pipeline.fuzzer.enabled:
            self.db.set_status(unit, Status.FUZZING)
            loop = await self.fuzz(unit, members, loop, dep_code, lessons)
        if loop.ok:
            await self.reflect(unit, members, loop)
            return self.accept(unit, members, loop, Status.VERIFIED)
        if not self.opts.gate and loop.code:
            return self.accept(unit, members, loop, Status.BLOCKED,
                               error="accepted without passing all tests (ungated baseline): " + (loop.error or ""))
        self.db.set_status(unit, Status.BLOCKED, error=loop.error, finished_at=time.time())
        return Status.BLOCKED

    async def fuzz(self, unit: str, members: list[str], loop: LoopResult, dep_code: dict[str, str],
                   lessons: list[dict[str, Any]]) -> LoopResult:
        """Adversarial rounds (Phase 6). Default: no-op."""
        return loop

    async def reflect(self, unit: str, members: list[str], loop: LoopResult) -> None:
        """Store lessons after a successful repair (Phase 6). Default: no-op."""

    def accept(self, unit: str, members: list[str], loop: LoopResult, status: Status, error: str | None = None) -> Status:
        for m in members:
            code = loop.code.get(self.adapter.target_name(m))
            if code is not None:
                self.db.set_module_fields(m, python_code=code)
        self.db.set_status(unit, status, error=error, finished_at=time.time(), model_used=loop.model)
        return status

    async def run_unit(self, unit: str, oracle: "asyncio.Task[str | None] | None") -> Status:
        """Oracle (awaiting an eager build if one is running) then process; never raises."""
        try:
            err = await oracle if oracle is not None else await self.ensure_oracle(unit)
            if err:
                self.db.set_status(unit, Status.BLOCKED, error=f"oracle: {err}", finished_at=time.time())
                return Status.BLOCKED
            return await self.process_unit(unit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a bug in one unit must not kill the run
            tb = traceback.format_exc(limit=6)
            self.db.emit("unit_crashed", unit, error=str(exc)[:500], traceback=tb[-2000:])
            self.db.set_status(unit, Status.BLOCKED, error=f"internal error: {type(exc).__name__}: {exc}",
                               finished_at=time.time())
            return Status.BLOCKED
