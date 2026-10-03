"""Read-only live dashboard: FastAPI JSON API + Server-Sent Events over the blackboard's `events` table.

The dashboard never writes migration state; it can run while `legacybridge migrate` is running
(SQLite WAL allows concurrent readers) and simply tails new event rows.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..db import Blackboard, Status

STATIC = Path(__file__).parent / "static"
POLL_S = 0.5
HEARTBEAT_S = 15.0


def state_snapshot(db: Blackboard) -> dict[str, Any]:
    """Units, unit-level dependency edges, counters and usage, for the overview page."""
    modules = {m["name"]: m for m in db.modules()}
    units = db.units()
    unit_of = {name: m["unit"] for name, m in modules.items()}
    nodes = []
    for u in units:
        kinds = {modules[m]["kind"] for m in u["members"] if m in modules}
        kind = "copybook" if kinds <= {"copybook"} else ("program" if "program" in kinds else "subprogram")
        nodes.append({
            "id": u["name"], "members": u["members"], "kind": kind, "status": u["status"],
            "attempts": u["attempts"], "fuzz_rounds_clean": u["fuzz_rounds_clean"],
            "fuzz_bugs_found": u["fuzz_bugs_found"], "oracle_done": bool(u["oracle_done"]),
            "model_used": u.get("model_used"), "error": u.get("error"),
            "seconds": round(u["finished_at"] - u["started_at"], 2) if u.get("finished_at") and u.get("started_at")
            else None,
        })
    edges: dict[tuple[str, str], str] = {}
    for e in db.edges():
        a, b = unit_of.get(e["src"]), unit_of.get(e["dst"])
        if a and b and a != b:
            # a call edge wins over a copy edge between the same two units
            edges[(a, b)] = "call" if e["kind"] == "call" or edges.get((a, b)) == "call" else "copy"
    tests = Counter()
    for r in db.query("SELECT origin, COUNT(*) n FROM test_cases GROUP BY origin"):
        tests[r["origin"]] = r["n"]
    calls = db.query("SELECT provider, model, status, SUM(cache_hit) cached, COUNT(*) n, "
                     "SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct, AVG(latency_ms) lat "
                     "FROM llm_calls GROUP BY provider, model, status ORDER BY provider, model, status")
    executable = [n for n in nodes if n["kind"] != "copybook"]
    last = db.one("SELECT MAX(id) m FROM events")
    return {
        "source_root": db.get_meta("source_root"),
        "units": nodes,
        "edges": [{"source": a, "target": b, "kind": k} for (a, b), k in sorted(edges.items())],
        "counts": dict(Counter(n["status"] for n in executable)),
        "total": len(executable),
        "tests": dict(tests),
        "lessons": [{"id": les["id"], "text": les["text"], "tags": les["tags"], "source_unit": les["source_unit"],
                     "uses": les["uses"]} for les in db.lessons()],
        "llm": [dict(r) for r in calls],
        "scenarios": db.get_meta("scenarios", []) or [],
        "last_event_id": (last["m"] if last else None) or 0,
        "terminal": [s.value for s in Status if s.terminal],
    }


def unit_detail(db: Blackboard, name: str) -> dict[str, Any] | None:
    """Everything about one unit: sources, attempts with verdicts/diffs, tests, final code."""
    u = db.unit(name)
    if u is None:
        return None
    members = []
    for m in u["members"]:
        mod = db.module(m) or {}
        tests = db.test_cases(m)
        members.append({
            "name": m, "kind": mod.get("kind"), "summary": mod.get("summary"), "source": mod.get("source"),
            "python_code": mod.get("python_code"),
            "features": (mod.get("info") or {}).get("features", []),
            "tests": dict(Counter(t["origin"] for t in tests)),
            "sample_tests": [{"origin": t["origin"], "name": t["name"], "input": t["input"][:2000],
                              "expected_stdout": t["expected_stdout"][:2000], "expected_exit": t["expected_exit"]}
                             for t in tests[:3]],
        })
    attempts = db.attempts(name)
    for a in attempts:   # attach the counterexample (stdin of the first failing test) per module
        for m, summ in (a["diff"] or {}).items():
            if not isinstance(summ, dict) or not summ.get("first_failure"):
                continue
            row = db.one("SELECT t.name, t.origin, t.input FROM verification_results v JOIN test_cases t "
                         "ON t.id = v.test_case_id WHERE v.attempt_id=? AND v.module=? AND v.passed=0 "
                         "ORDER BY v.id LIMIT 1", (a["id"], m))
            if row:
                summ["first_failure"] = {**summ["first_failure"], "name": f"{row['origin']}:{row['name'] or ''}",
                                         "input": row["input"][:2000]}
    events = db.query("SELECT id FROM events WHERE unit=? ORDER BY id DESC LIMIT 1", (name,))
    return {
        **u, "members": members,
        "attempts": attempts,
        "deps": sorted({r["dst"] for r in db.query(
            "SELECT e.dst FROM edges e JOIN modules m ON m.name = e.src WHERE m.unit=?", (name,))}),
        "last_event_id": events[0]["id"] if events else None,
    }


def _max_event_id(db: Blackboard) -> int:
    row = db.one("SELECT COALESCE(MAX(id), 0) m FROM events")
    return int(row["m"]) if row else 0


async def event_stream(db: Blackboard, last_id: int, request: Request | None = None,
                       poll_s: float = POLL_S, max_s: float | None = None) -> AsyncIterator[str]:
    """SSE frames for every event with id > last_id, forever (or for max_s seconds)."""
    started = beat = time.monotonic()
    yield "retry: 2000\n\n"
    while True:
        if request is not None and await request.is_disconnected():
            return
        if last_id and await asyncio.to_thread(_max_event_id, db) < last_id:
            last_id = 0                      # the state db was reset by a fresh run
            yield "event: reset\ndata: {}\n\n"
        rows = await asyncio.to_thread(db.events_after, last_id, 500)
        for ev in rows:
            last_id = ev["id"]
            yield f"id: {ev['id']}\nevent: ev\ndata: {json.dumps(ev, default=str)}\n\n"
        now = time.monotonic()
        if max_s is not None and now - started >= max_s:
            return
        if not rows:
            if now - beat >= HEARTBEAT_S:
                beat = now
                yield ": keep-alive\n\n"
            await asyncio.sleep(poll_s)


def create_app(db_path: str | Path) -> FastAPI:
    """Dashboard app bound to one state database (created empty if missing)."""
    db = Blackboard(db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        db.close()

    app = FastAPI(title="LegacyBridge dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db = db

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    def api_state() -> JSONResponse:
        return JSONResponse(state_snapshot(db))

    @app.get("/api/unit/{name}")
    def api_unit(name: str) -> JSONResponse:
        d = unit_detail(db, name)
        if d is None:
            raise HTTPException(404, f"unknown unit {name}")
        return JSONResponse(json.loads(json.dumps(d, default=str)))

    @app.get("/api/events")
    def api_events(after: int = 0, limit: int = 500) -> JSONResponse:
        return JSONResponse(db.events_after(after, min(max(limit, 1), 5000)))

    @app.get("/api/stream")
    async def api_stream(request: Request, after: int | None = None) -> StreamingResponse:
        hdr = request.headers.get("last-event-id")
        start = after if after is not None else int(hdr) if hdr and hdr.isdigit() else 0
        return StreamingResponse(event_stream(db, start, request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
