"""Async work queue over migration units.

A unit becomes READY when every unit it depends on is VERIFIED; a BLOCKED unit marks all its
transitive dependents DEP_BLOCKED. Oracle builds don't depend on translations, so they start
eagerly for every pending unit (bounded by `oracle_concurrency`) while earlier waves migrate.
All state lives in the blackboard, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from .db import Status
from .pipeline import Pipeline

IN_FLIGHT = {Status.ORACLE, Status.READY, Status.TRANSLATING, Status.VERIFYING, Status.REPAIRING, Status.FUZZING}
WAITING = {Status.PENDING, Status.ORACLE}


class Scheduler:
    """Runs the pipeline over all units, dependencies first, `concurrency` units at a time."""

    def __init__(self, pipeline: Pipeline, concurrency: int | None = None, oracle_concurrency: int | None = None):
        self.p = pipeline
        cfg = pipeline.settings.pipeline.scheduler
        self.concurrency = max(1, concurrency or cfg.concurrency)
        self.oracle_concurrency = max(1, oracle_concurrency or cfg.oracle_concurrency)

    # ------------------------------------------------------------ state
    def status(self, unit: str) -> Status:
        return Status((self.p.db.unit(unit) or {}).get("status", Status.PENDING))

    def reset_interrupted(self, retry_blocked: bool = False) -> list[str]:
        """Units left mid-flight by a previous (killed) run go back to PENDING; returns them."""
        reset = []
        for u in self.p.db.units():
            st = Status(u["status"])
            external = st == Status.DEP_BLOCKED and (u.get("error") or "").startswith("calls ")
            if st in IN_FLIGHT or (retry_blocked and st in (Status.BLOCKED, Status.DEP_BLOCKED) and not external):
                self.p.db.set_status(u["name"], Status.PENDING, error=None)
                reset.append(u["name"])
        if reset:
            self.p.db.emit("resume", None, reset=reset)
        return reset

    def _deps_done(self, unit: str) -> bool:
        for dep in self.p.plan.deps_of(unit):
            st = self.status(dep)
            if st == Status.VERIFIED:
                continue
            if not self.p.opts.gate and st == Status.BLOCKED and any(
                    (self.p.db.module(m) or {}).get("python_code") for m in self.p.members(dep)):
                continue
            return False
        return True

    def ready_units(self, running: set[str]) -> list[str]:
        return [u for wave in self.p.plan.order for u in wave
                if u not in running and self.status(u) in WAITING and self._deps_done(u)]

    def block_dependents(self, unit: str) -> None:
        if not self.p.opts.gate:
            return
        for d in sorted(self.p.plan.transitive_dependents(unit)):
            if not self.status(d).terminal:
                self.p.db.set_status(d, Status.DEP_BLOCKED, error=f"depends on {unit}, which is blocked",
                                     finished_at=time.time())

    # ------------------------------------------------------------ run
    async def _oracle(self, unit: str, sem: asyncio.Semaphore) -> str | None:
        async with sem:
            if self.status(unit) == Status.PENDING:
                self.p.db.set_status(unit, Status.ORACLE)
            try:
                return await self.p.ensure_oracle(unit)
            except Exception as exc:  # noqa: BLE001 - reported as the unit's oracle error
                return f"{type(exc).__name__}: {exc}"
            finally:
                if self.status(unit) == Status.ORACLE:
                    self.p.db.set_status(unit, Status.PENDING)

    async def run(self, retry_blocked: bool = False) -> dict[str, int]:
        """Migrate every pending unit. Returns a count of final statuses."""
        db = self.p.db
        self.reset_interrupted(retry_blocked)
        db.emit("run_started", None, concurrency=self.concurrency,
                units=[u["name"] for u in db.units() if not Status(u["status"]).terminal])
        t0 = time.time()
        sem = asyncio.Semaphore(self.oracle_concurrency)
        oracles = {u["name"]: asyncio.create_task(self._oracle(u["name"], sem))
                   for u in db.units() if Status(u["status"]) in WAITING and not u.get("oracle_done")}
        running: dict[asyncio.Task, str] = {}
        try:
            while True:
                for u in self.ready_units(set(running.values())):
                    if len(running) >= self.concurrency:
                        break
                    db.set_status(u, Status.READY)
                    running[asyncio.create_task(self.p.run_unit(u, oracles.get(u)))] = u
                if not running:
                    break
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    unit = running.pop(task)
                    if task.result() != Status.VERIFIED and self.status(unit) == Status.BLOCKED:
                        self.block_dependents(unit)
        finally:
            for t in list(running) + list(oracles.values()):
                if not t.done():
                    t.cancel()
            await asyncio.gather(*running, *oracles.values(), return_exceptions=True)
        counts = Counter(u["status"] for u in db.units())
        db.emit("run_finished", None, seconds=round(time.time() - t0, 1), **counts)
        return dict(counts)
