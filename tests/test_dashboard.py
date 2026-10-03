"""Dashboard API (FastAPI TestClient) and the SSE event stream, offline (no cobc needed)."""

import json

from fastapi.testclient import TestClient

from conftest import BANK_DEMO
from legacybridge.adapters import get_adapter
from legacybridge.agents.analyzer import analyze
from legacybridge.dashboard.app import create_app, event_stream
from legacybridge.db import Blackboard, Status


def seeded(tmp_path):
    db = Blackboard(tmp_path / "state.db")
    ad = get_adapter("cobol-python", BANK_DEMO, work_dir=tmp_path / "work")
    analyze(ad, db)
    db.set_meta("source_root", str(BANK_DEMO))
    db.set_status("FMTUTIL", Status.VERIFIED, attempts=2, model_used="mock/mock")
    db.set_status("BALUPD", Status.BLOCKED, error="repair budget exhausted")
    tid = db.add_test_case("FMTUTIL", "llm", "A|x\n", "out\n", 0, name="case-1")
    a1 = db.add_attempt("FMTUTIL", 1, "translate", "mock/mock", {"fmtutil": "def fmtutil(p): pass\n"})
    db.finish_attempt(a1, "fail", {"FMTUTIL": {"passed": 0, "total": 1, "static_error": None,
                                               "first_failure": {"kind": "stdout", "expected": "out", "actual": ""}}},
                      "oracle")
    db.add_verification_results(a1, [{"module": "FMTUTIL", "test_case_id": tid, "passed": False,
                                      "actual_stdout": "", "diff": {"kind": "stdout"}}])
    db.add_lesson("Pad PIC X fields to full width.", ["display-trailing-spaces"], "FMTUTIL")
    return db


def test_state_and_unit_endpoints(tmp_path):
    db = seeded(tmp_path)
    with TestClient(create_app(tmp_path / "state.db")) as client:
        assert "cytoscape" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200
        st = client.get("/api/state").json()
        units = {u["id"]: u for u in st["units"]}
        assert units["FMTUTIL"]["status"] == "verified" and units["FMTUTIL"]["kind"] == "subprogram"
        assert units["BALUPD"]["kind"] == "program" and units["BALUPD"]["error"] == "repair budget exhausted"
        assert any(u["kind"] == "copybook" for u in st["units"])
        assert {"source": "BALUPD", "target": "FEECALC", "kind": "call"} in st["edges"]
        assert st["counts"]["verified"] == 1 and st["total"] == 10
        assert st["lessons"][0]["tags"] == ["display-trailing-spaces"] and st["tests"] == {"llm": 1}

        u = client.get("/api/unit/FMTUTIL").json()
        assert "FMTUTIL" in u["members"][0]["source"] and u["members"][0]["tests"] == {"llm": 1}
        ff = u["attempts"][0]["diff"]["FMTUTIL"]["first_failure"]
        assert ff["input"] == "A|x\n" and ff["name"] == "llm:case-1"        # the counterexample is attached
        assert client.get("/api/unit/NOPE").status_code == 404

        evs = client.get("/api/events?after=0").json()
        assert evs and all(e["id"] > 0 for e in evs)
        assert client.get(f"/api/events?after={evs[-1]['id']}").json() == []
    db.close()


async def test_event_stream_frames_and_reset(tmp_path):
    db = Blackboard(tmp_path / "state.db")
    db.emit("run_started", None, concurrency=2)
    db.emit("lesson_added", "X", lesson_id=7, text="t", tags=[])
    frames = [f async for f in event_stream(db, 0, poll_s=0.01, max_s=0.05)]
    assert frames[0].startswith("retry:")
    data = [json.loads(f.split("data: ", 1)[1]) for f in frames if f.startswith("id:")]
    assert [d["type"] for d in data] == ["run_started", "lesson_added"]
    assert data[1]["id"] == 2 and data[1]["lesson_id"] == 7      # payload never overrides the event id
    last = data[-1]["id"]
    db.reset()                                                   # a fresh `migrate` in the same state dir
    db.emit("run_started", None, concurrency=1)
    frames = [f async for f in event_stream(db, last, poll_s=0.01, max_s=0.05)]
    assert any(f.startswith("event: reset") for f in frames)
    assert any('"run_started"' in f for f in frames if f.startswith("id:"))
    db.close()
