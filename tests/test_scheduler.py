"""Scheduler: dependency gating, concurrency, BLOCKED propagation, crash isolation, resume; migrate CLI."""

import asyncio

import pytest
from typer.testing import CliRunner

from conftest import BANK_DEMO, requires_cobc
from legacybridge.db import Blackboard, Status
from legacybridge.graph import build_plan
from legacybridge.pipeline import Pipeline, PipelineOptions
from legacybridge.scheduler import Scheduler

#   E -> C -> A          (E depends on C, C on A)
#   D -> B, D -> C
#   F -> E
EDGES = [("C", "A", "call"), ("D", "B", "call"), ("D", "C", "call"), ("E", "C", "call"), ("F", "E", "call")]


class FakePipeline(Pipeline):
    """Pipeline whose stages are scripted (no LLM, no sandbox)."""

    def __init__(self, db, plan, settings, fail=(), crash=(), delay=0.02):
        super().__init__(adapter=None, db=db, llm=None, settings=settings, plan=plan, options=PipelineOptions())
        self.fail, self.crash, self.delay = set(fail), set(crash), delay
        self.order: list[str] = []
        self.active = 0
        self.max_active = 0
        self.oracles: list[str] = []

    def members(self, unit):
        return self.plan.units[unit]

    async def ensure_oracle(self, unit):
        self.oracles.append(unit)
        self.db.update_unit(unit, oracle_done=1)
        return None

    async def process_unit(self, unit):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            for dep in self.plan.deps_of(unit):
                assert self.db.unit(dep)["status"] == Status.VERIFIED, f"{unit} started before {dep}"
            self.order.append(unit)
            self.db.set_status(unit, Status.TRANSLATING, attempts=1)
            await asyncio.sleep(self.delay)
            if unit in self.crash:
                raise RuntimeError("boom")
            st = Status.BLOCKED if unit in self.fail else Status.VERIFIED
            self.db.set_status(unit, st, error="scripted failure" if unit in self.fail else None)
            return st
        finally:
            self.active -= 1


def setup(tmp_path, settings_factory):
    plan = build_plan({n: {"kind": "program", "executable": True} for n in "ABCDEF"}, EDGES)
    db = Blackboard(tmp_path / "state.db")
    for u, ms in plan.units.items():
        db.upsert_unit(u, ms)
    return db, plan, settings_factory(dry_run=True)


async def test_dependencies_first_and_concurrency(tmp_path, settings_factory):
    db, plan, settings = setup(tmp_path, settings_factory)
    pipe = FakePipeline(db, plan, settings)
    counts = await Scheduler(pipe, concurrency=2).run()
    assert counts == {"verified": 6}
    assert pipe.max_active <= 2 and pipe.max_active == 2          # A and B ran in parallel
    assert pipe.order.index("A") < pipe.order.index("C") < pipe.order.index("E") < pipe.order.index("F")
    assert sorted(pipe.oracles) == list("ABCDEF")
    types = [e["type"] for e in db.events_after(0, limit=10000)]
    assert types[-1] == "run_finished" and "run_started" in types


async def test_blocked_propagates_and_crash_is_isolated(tmp_path, settings_factory):
    db, plan, settings = setup(tmp_path, settings_factory)
    pipe = FakePipeline(db, plan, settings, fail={"C"}, crash={"B"})
    await Scheduler(pipe, concurrency=4).run()
    st = {u["name"]: u["status"] for u in db.units()}
    assert st["A"] == Status.VERIFIED
    assert st["B"] == Status.BLOCKED and "internal error: RuntimeError" in db.unit("B")["error"]
    assert st["C"] == Status.BLOCKED
    assert st["D"] == st["E"] == st["F"] == Status.DEP_BLOCKED
    assert "D" not in pipe.order and "F" not in pipe.order
    assert any(e["type"] == "unit_crashed" for e in db.events_after(0, limit=10000))


async def test_resume_after_interruption(tmp_path, settings_factory):
    db, plan, settings = setup(tmp_path, settings_factory)
    db.set_status("A", Status.VERIFIED)
    db.set_status("B", Status.VERIFIED)
    db.set_status("C", Status.REPAIRING)          # killed mid-repair
    db.set_status("D", Status.BLOCKED, error="old failure")
    pipe = FakePipeline(db, plan, settings)
    await Scheduler(pipe).run()
    assert "A" not in pipe.order and "B" not in pipe.order       # verified work is kept
    assert pipe.order[0] == "C"
    assert db.unit("D")["status"] == Status.BLOCKED             # not retried without --retry-blocked
    assert db.unit("F")["status"] == Status.VERIFIED
    pipe2 = FakePipeline(db, plan, settings)
    await Scheduler(pipe2).run(retry_blocked=True)
    assert pipe2.order == ["D"] and db.unit("D")["status"] == Status.VERIFIED


@requires_cobc
def test_migrate_cli_dry_run_and_resume(tmp_path):
    from legacybridge.cli import app
    runner = CliRunner()
    state = str(tmp_path / "state")
    res = runner.invoke(app, ["migrate", str(BANK_DEMO), "--dry-run", "--state", state, "-q"])
    assert res.exit_code == 0, res.output
    assert "18/18 units verified" in res.output
    db = Blackboard(tmp_path / "state" / "state.db")
    assert db.unit("BALUPD")["attempts"] == 3
    db.set_status("LOANSCHD", Status.TRANSLATING)               # simulate a kill mid-unit
    db.close()
    res = runner.invoke(app, ["migrate", str(BANK_DEMO), "--dry-run", "--state", state, "--resume", "-q"])
    assert res.exit_code == 0, res.output
    db = Blackboard(tmp_path / "state" / "state.db")
    resumed = [e for e in db.events_after(0, limit=100000) if e["type"] == "resume"]
    assert resumed and resumed[-1]["reset"] == ["LOANSCHD"]
    assert db.unit("LOANSCHD")["status"] == Status.VERIFIED
    db.close()
    res = runner.invoke(app, ["verify", str(BANK_DEMO), "--state", state])
    assert res.exit_code == 0 and "FAIL" not in res.output, res.output
