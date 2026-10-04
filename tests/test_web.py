"""Website backend: punch-card accounts, sessions, run ownership, uploads, and a real demo run."""

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import BANK_DEMO, REPO, requires_cobc
from legacybridge.config import load_settings
from legacybridge.web.app import create_web_app
from legacybridge.web.runs import Upload, UploadError, place_uploads
from legacybridge.web.store import WebStore
from legacybridge.web.supabase import SupabaseAuthError, SupabaseConfig


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)

    def make(spawn: bool = False, **kw) -> TestClient:
        app = create_web_app(tmp_path / "home", load_settings(REPO / "config", dry_run=True),
                             demo_root=BANK_DEMO, start_specimen=False, **kw)
        if not spawn:
            monkeypatch.setattr(app.state.runs, "start", lambda run: None)
        return TestClient(app)
    return make


def _punch(c: TestClient, handle: str, pw: str = "correct horse", new: bool = True):
    return c.post("/api/auth/signup" if new else "/api/auth/signin", json={"handle": handle, "passphrase": pw})


def test_store_hashes_and_sessions(tmp_path):
    s = WebStore(tmp_path / "web.db")
    u = s.create_user("Grace.Hopper", "cobol-1959")
    assert u["handle"] == "grace.hopper"
    row = s.conn.execute("SELECT pw_hash FROM users").fetchone()[0]
    assert b"cobol-1959" not in row and len(row) == 32
    assert s.authenticate("grace.hopper", "wrong pass") is None
    assert s.authenticate("GRACE.HOPPER", "cobol-1959")["id"] == u["id"]
    tok = s.new_session(u["id"])
    assert s.session_user(tok)["handle"] == "grace.hopper"
    assert tok not in str(s.conn.execute("SELECT * FROM sessions").fetchall())   # only the hash is stored
    s.end_session(tok)
    assert s.session_user(tok) is None


def test_punch_in_flow(make_client):
    c = make_client()
    assert c.get("/api/session").json()["user"] is None
    assert c.get("/api/auth/handle/ada").json() == {"exists": False}
    assert _punch(c, "ada", "short").status_code == 400
    assert _punch(c, "../ada").status_code == 400
    r = _punch(c, "ada")
    assert r.status_code == 200 and r.json()["new"] is True
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/api/session").json()["user"]["handle"] == "ada"
    assert _punch(c, "ada").status_code == 400                      # taken
    c.post("/api/auth/signout")
    assert c.get("/api/session").json()["user"] is None
    assert _punch(c, "ada", "wrong passphrase", new=False).status_code == 401
    assert _punch(c, "ada", new=False).status_code == 200
    assert c.get("/api/auth/handle/ada").json() == {"exists": True}


def test_pages_served(make_client):
    c = make_client()
    for path in ("/", "/app"):
        r = c.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert c.get("/api/session").json()["supabase"] is None


def test_supabase_unconfigured(make_client):
    c = make_client()
    assert c.post("/api/auth/supabase", json={"access_token": "x"}).status_code == 404


def test_supabase_sign_in(make_client):
    users = {"tok-a": {"id": "uuid-a", "app_metadata": {"provider": "github"}, "user_metadata": {"user_name": "Ada"}},
             "tok-b": {"id": "uuid-b", "email": "ada@example.com"}}

    def verify(token):
        if token not in users:
            raise SupabaseAuthError("token rejected")
        return users[token]

    c = make_client(supabase=SupabaseConfig("https://x.supabase.co", "anon"), supabase_verifier=verify)
    assert c.get("/api/session").json()["supabase"] == {"url": "https://x.supabase.co", "anon_key": "anon"}
    r = c.post("/api/auth/supabase", json={"access_token": "tok-a"})
    assert r.status_code == 200 and r.json()["user"]["handle"] == "ada" and r.json()["provider"] == "github"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/session").json()["user"]["handle"] == "ada"
    c.post("/api/auth/signout")
    assert c.post("/api/auth/supabase", json={"access_token": "tok-a"}).json()["user"]["handle"] == "ada"   # linked
    other = c.post("/api/auth/supabase", json={"access_token": "tok-b"}).json()
    assert other["user"]["handle"] == "ada-2" and other["provider"] == "email"
    assert c.post("/api/auth/supabase", json={"access_token": "forged"}).status_code == 401


def test_supabase_handles_unique(tmp_path):
    s = WebStore(tmp_path / "web.db")
    s.create_user("grace", "cobol-1959")
    assert s.user_for_supabase("u1", "Grace")["handle"] == "grace-2"
    assert s.user_for_supabase("u1", "whatever")["handle"] == "grace-2"
    assert s.user_for_supabase("u2", "jo")["handle"] == "jo-lb"
    assert s.user_for_supabase("u3", "!!!")["handle"] == "punched"
    assert s.authenticate("grace-2", "") is None


def test_cross_origin_writes_refused(make_client):
    c = make_client()
    r = c.post("/api/auth/signup", json={"handle": "eve", "passphrase": "12345678"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_runs_are_private(make_client):
    c = make_client()
    assert c.post("/api/runs", json={"source": "demo"}).status_code == 401
    _punch(c, "ada")
    run = c.post("/api/runs", json={"source": "demo", "fuzz": False}).json()
    assert run["status"] == "queued" and run["dry_run"] is True and run["public"] is False
    assert [r["id"] for r in c.get("/api/runs").json()] == [run["id"]]
    assert c.get(f"/api/runs/{run['id']}/state").status_code == 200
    # live runs need keys on the server
    r = c.post("/api/runs", json={"source": "demo", "live": True})
    assert r.status_code in (201, 400)

    other = make_client()
    other.cookies.clear()
    assert other.get(f"/api/runs/{run['id']}").status_code == 404           # anonymous
    _punch(other, "bob")
    assert other.get(f"/api/runs/{run['id']}/state").status_code == 404     # someone else
    assert other.delete(f"/api/runs/{run['id']}").status_code == 404
    assert c.delete(f"/api/runs/{run['id']}").json() == {"ok": True}
    assert c.get("/api/runs").json() == []


def test_upload_validation(tmp_path, make_client):
    prog = (BANK_DEMO / "src" / "INTCALC.cbl").read_bytes()
    cpy = (BANK_DEMO / "copybooks" / "INTPARM.cpy").read_bytes()
    counts = place_uploads([Upload("../../etc/INTCALC.cbl", prog), Upload("INTPARM.cpy", cpy)], tmp_path / "a")
    assert counts == {"programs": 1, "copybooks": 1}
    assert (tmp_path / "a" / "src" / "INTCALC.cbl").exists()            # path stripped to the basename
    for files, msg in [([Upload("INTPARM.cpy", cpy)], "at least one"),
                       ([Upload("x.exe", b"MZ")], "expected"),
                       ([Upload("we ird.cbl", prog)], "Bad file name"),
                       ([Upload("bin.cbl", b"\xff\xfe\x00")], "UTF-8"),
                       ([Upload("A.cbl", prog), Upload("a.cbl", prog)], "Duplicate")]:
        with pytest.raises(UploadError, match=msg):
            place_uploads(files, tmp_path / "b")

    c = make_client()
    _punch(c, "ada")
    r = c.post("/api/runs", json={"source": "upload", "files": [{"name": "notes.md", "content": "hi"}]})
    assert r.status_code == 400 and "expected" in r.json()["detail"]
    assert c.get("/api/runs").json() == []


@requires_cobc
def test_demo_run_end_to_end(make_client):
    with make_client(spawn=True) as c:
        _punch(c, "ada")
        run = c.post("/api/runs", json={"source": "demo", "fuzz": False}).json()
        deadline = time.time() + 240
        while c.get(f"/api/runs/{run['id']}").json()["status"] in ("queued", "running"):
            assert time.time() < deadline, "demo run did not finish"
            time.sleep(0.5)
        done = c.get(f"/api/runs/{run['id']}").json()
        assert done["status"] == "done", done["note"]
        s = done["summary"]
        assert s["verified"] == s["units"] == 10 and s["package"] and s["scenarios"] == 4
        # without fuzzing, STMTRPT's long-statement page break slips through: the scenario catches it
        assert s["scenarios_identical"] == 3
        state = c.get(f"/api/runs/{run['id']}/state").json()
        assert state["counts"] == {"verified": 10}
        assert c.get(f"/api/runs/{run['id']}/unit/STMTRPT").json()["attempts"]
        ev = c.get(f"/api/runs/{run['id']}/events").json()
        assert ev[0]["type"] and any(e["type"] == "run_finished" for e in ev)
        assert "LegacyBridge" in c.get(f"/api/runs/{run['id']}/report").text
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/runs/{run['id']}/download").content))
        names = z.namelist()
        assert "REPORT.md" in names and any(n.endswith("intcalc.py") for n in names)
