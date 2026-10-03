from legacybridge.db import Blackboard, Status


def test_units_status_events_and_resume(tmp_path):
    db = Blackboard(tmp_path / "s.db")
    db.upsert_unit("U1", ["A", "B"])
    db.set_status("U1", Status.TRANSLATING, attempts=1)
    db.upsert_unit("U1", ["A", "B"])  # re-analysis must not reset status (resume)
    u = db.unit("U1")
    assert u["status"] is Status.TRANSLATING and u["attempts"] == 1 and u["members"] == ["A", "B"]
    ev = db.events_after(0)
    assert ev[-1]["type"] == "status" and ev[-1]["status"] == "translating"
    db.close()
    db2 = Blackboard(tmp_path / "s.db")
    assert db2.unit("U1")["status"] is Status.TRANSLATING


def test_test_cases_dedupe(tmp_path):
    db = Blackboard(tmp_path / "s.db")
    assert db.add_test_case("M", "llm", "in\n", "out\n", 0) is not None
    assert db.add_test_case("M", "random", "in\n", "out\n", 0) is None
    assert db.has_input("M", "in\n")
    assert len(db.test_cases("M")) == 1
    assert db.test_cases("M", origins=["random"]) == []


def test_attempts_lessons_llm_usage(tmp_path):
    db = Blackboard(tmp_path / "s.db")
    aid = db.add_attempt("U", 1, "translate", "mock/x", {"A": "print(1)"}, lessons=[1])
    db.finish_attempt(aid, "fail", {"failed": 1}, "recorded")
    a = db.attempts("U")[0]
    assert a["code"] == {"A": "print(1)"} and a["diff"] == {"failed": 1} and a["verdict"] == "fail"
    lid = db.add_lesson("use ROUND_HALF_UP", ["rounded", "comp3"], "U")
    db.bump_lesson_uses([lid])
    assert db.lessons()[0]["uses"] == 1
    db.log_llm_call(provider="groq", model="m", prompt_tokens=10, completion_tokens=5, status="ok", cache_hit=0)
    db.log_llm_call(provider="groq", model="m", prompt_tokens=10, completion_tokens=5, status="ok", cache_hit=1)
    assert db.llm_usage_since("groq", "m", 0) == (1, 15)
