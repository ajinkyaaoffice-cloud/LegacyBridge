"""The LegacyBridge website: the story at `/`, the product at `/app`, served by FastAPI.

Everything the page shows comes from the same read-only views as the dashboard
(`state_snapshot`, `unit_detail`, the SSE event stream), scoped to one run. A public "specimen"
run (the demo corpus, offline) fills the page for visitors; signed-in users feed their own.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import REPO_ROOT, Settings, load_settings
from ..dashboard.app import event_stream, state_snapshot, unit_detail
from ..db import Blackboard
from .runs import RunManager, Upload, UploadError, live_keys_available
from .store import AuthError, WebStore
from .supabase import Mirror, SupabaseAuthError, SupabaseConfig, Verifier, http_verifier, identity

STATIC = Path(__file__).parent / "static"
COOKIE = "lb_session"
SPECIMEN = "specimen"
DEMO_ROOT = REPO_ROOT / "examples" / "bank_demo"


class Credentials(BaseModel):
    handle: str = Field(max_length=64)
    passphrase: str = Field(max_length=256)


class SupabaseToken(BaseModel):
    access_token: str = Field(max_length=8192)


class UploadFile(BaseModel):
    name: str = Field(max_length=128)
    content: str


class NewRun(BaseModel):
    source: str = "demo"                 # demo | upload (files from disk or a GitHub repo cached in the browser)
    title: str | None = Field(default=None, max_length=80)
    files: list[UploadFile] = []
    fuzz: bool = True
    live: bool = False                   # real free-tier APIs (needs keys); default: offline MockProvider


def create_web_app(home: Path, settings: Settings | None = None, demo_root: Path = DEMO_ROOT,
                   start_specimen: bool = True, secure_cookies: bool = False,
                   supabase: SupabaseConfig | None = None, supabase_verifier: Verifier | None = None) -> FastAPI:
    """Website bound to a home dir holding web.db and runs/.

    `supabase` (default: from SUPABASE_* env vars) enables hosted sign-in and the run mirror;
    `supabase_verifier` replaces the HTTP token check (tests).
    """
    home = home.resolve()
    settings = settings or load_settings(dry_run=True)
    store = WebStore(home / "web.db")
    supabase = supabase if supabase is not None else SupabaseConfig.from_env()
    verify = supabase_verifier or (http_verifier(supabase) if supabase else None)
    mirror = Mirror(supabase, store.supabase_id) if supabase and supabase.service_key else None
    if mirror:
        store.run_listeners.append(mirror.run_changed)
    runs = RunManager(home, store, settings, demo_root)
    boards: dict[str, Blackboard] = {}

    def board(run_id: str) -> Blackboard:
        if run_id not in boards:
            path = runs.state_db(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            boards[run_id] = Blackboard(path)
        return boards[run_id]

    def ensure_specimen() -> None:
        sp = store.run(SPECIMEN)
        if sp and sp["status"] == "done" and runs.state_db(SPECIMEN).exists():
            return
        if sp:
            runs.remove(SPECIMEN)
            boards.pop(SPECIMEN, None)
        run = store.create_run(None, "Specimen: bank_demo", "demo", dry_run=True, fuzz=True, run_id=SPECIMEN)
        runs.prepare_demo(SPECIMEN)
        runs.start(run)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runs.recover()
        if start_specimen:
            ensure_specimen()
        yield
        await runs.shutdown()
        for b in boards.values():
            b.close()
        if mirror:
            mirror.close()
        store.close()

    app = FastAPI(title="LegacyBridge", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store, app.state.runs = store, runs

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        """Reject cross-site writes (cookies are SameSite=Lax too)."""
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        response = await call_next(request)
        if request.url.path in ("/", "/app") or request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-cache"   # revalidate (ETag), never serve a stale build
        return response

    # ------------------------------------------------------------ identity
    def current_user(request: Request) -> dict[str, Any] | None:
        return store.session_user(request.cookies.get(COOKIE))

    def require_user(user: dict[str, Any] | None = Depends(current_user)) -> dict[str, Any]:
        if user is None:
            raise HTTPException(401, "punch in first")
        return user

    def readable_run(run_id: str, user: dict[str, Any] | None = Depends(current_user)) -> dict[str, Any]:
        run = store.run(run_id)
        if run is None or (run["user_id"] is not None and (user is None or user["id"] != run["user_id"])):
            raise HTTPException(404, "no such run")
        return run

    def owned_run(run: dict[str, Any] = Depends(readable_run), user: dict[str, Any] = Depends(require_user)):
        if run["user_id"] != user["id"]:
            raise HTTPException(403, "not your run")
        return run

    def _set_cookie(resp: Response, token: str) -> None:
        resp.set_cookie(COOKIE, token, max_age=30 * 24 * 3600, httponly=True, samesite="lax",
                        secure=secure_cookies, path="/")

    def _public_user(u: dict[str, Any]) -> dict[str, Any]:
        return {"handle": u["handle"], "since": u.get("created_at")}

    @app.get("/api/session")
    def api_session(user: dict[str, Any] | None = Depends(current_user)) -> dict[str, Any]:
        ok, msg = _toolchain()
        return {"user": _public_user(user) if user else None, "live_providers": live_keys_available(settings),
                "cobc": ok, "cobc_note": None if ok else msg, "specimen": SPECIMEN,
                "supabase": supabase.public() if supabase else None}

    @app.post("/api/auth/signup")
    def api_signup(creds: Credentials, response: Response) -> dict[str, Any]:
        try:
            user = store.create_user(creds.handle, creds.passphrase)
        except AuthError as exc:
            raise HTTPException(400, str(exc)) from None
        _set_cookie(response, store.new_session(user["id"]))
        return {"user": _public_user(user), "new": True}

    @app.post("/api/auth/signin")
    def api_signin(creds: Credentials, response: Response) -> dict[str, Any]:
        user = store.authenticate(creds.handle, creds.passphrase)
        if user is None:
            raise HTTPException(401, "That card doesn't match. Check the handle and passphrase.")
        _set_cookie(response, store.new_session(user["id"]))
        return {"user": _public_user(user), "new": False}

    @app.get("/api/auth/handle/{handle}")
    def api_handle(handle: str) -> dict[str, bool]:
        """Is this handle already punched? (lets the card switch between sign in and new card)"""
        return {"exists": store.user_by_handle(handle) is not None}

    @app.post("/api/auth/supabase")
    def api_supabase(body: SupabaseToken, response: Response) -> dict[str, Any]:
        """Exchange a Supabase Auth access token (e-mail or GitHub sign-in) for a site session."""
        if verify is None:
            raise HTTPException(404, "Supabase is not configured on this server.")
        try:
            sb_user = verify(body.access_token)
        except SupabaseAuthError as exc:
            raise HTTPException(401, str(exc)) from None
        uid, preferred, provider = identity(sb_user)
        user = store.user_for_supabase(uid, preferred)
        if mirror:
            mirror.profile(uid, user["handle"], provider)
        _set_cookie(response, store.new_session(user["id"]))
        return {"user": _public_user(user), "new": False, "provider": provider}

    @app.post("/api/auth/signout")
    def api_signout(request: Request, response: Response) -> dict[str, bool]:
        store.end_session(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # ------------------------------------------------------------ runs
    def _public_run(run: dict[str, Any]) -> dict[str, Any]:
        out = {k: run[k] for k in ("id", "title", "source_kind", "dry_run", "fuzz", "status", "exit_code", "note",
                                   "created_at", "started_at", "finished_at", "summary")}
        out["public"] = run["user_id"] is None
        out["has_package"] = (runs.out_dir(run["id"]) / "REPORT.md").exists()
        return out

    @app.get("/api/runs")
    def api_runs(user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        return [_public_run(r) for r in store.runs_for(user["id"])]

    @app.post("/api/runs", status_code=201)
    async def api_new_run(body: NewRun, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        if body.source not in ("demo", "upload"):
            raise HTTPException(400, "source must be demo or upload")
        if body.live and not live_keys_available(settings):
            raise HTTPException(400, "No API keys configured on this server: add GEMINI_API_KEY, GROQ_API_KEY, "
                                     "OPENROUTER_API_KEY or MISTRAL_API_KEY to .env, or run offline.")
        active = [r for r in store.runs_for(user["id"]) if r["status"] in ("queued", "running")]
        if len(active) >= 2:
            raise HTTPException(429, "Two runs are already in the machine. Wait for one to finish.")
        title = (body.title or "").strip() or ("bank_demo" if body.source == "demo" else
                                                f"{len(body.files)} file deck")
        run = store.create_run(user["id"], title, body.source, dry_run=not body.live, fuzz=body.fuzz)
        try:
            if body.source == "demo":
                await asyncio.to_thread(runs.prepare_demo, run["id"])
            else:
                await asyncio.to_thread(runs.prepare_upload, run["id"],
                                        [Upload(f.name, f.content.encode()) for f in body.files])
        except UploadError as exc:
            store.delete_run(run["id"])
            raise HTTPException(400, str(exc)) from None
        runs.start(run)   # needs the event loop: this endpoint is async on purpose
        return _public_run(store.run(run["id"]))  # type: ignore[arg-type]

    @app.get("/api/runs/{run_id}")
    def api_run(run: dict[str, Any] = Depends(readable_run)) -> dict[str, Any]:
        return _public_run(run)

    @app.delete("/api/runs/{run_id}")
    def api_delete_run(run: dict[str, Any] = Depends(owned_run)) -> dict[str, bool]:
        if runs.is_active(run["id"]):
            raise HTTPException(409, "This run is still in the machine.")
        b = boards.pop(run["id"], None)
        if b:
            b.close()
        runs.remove(run["id"])
        return {"ok": True}

    @app.get("/api/runs/{run_id}/state")
    def api_run_state(run: dict[str, Any] = Depends(readable_run)) -> JSONResponse:
        return JSONResponse(json.loads(json.dumps(state_snapshot(board(run["id"])), default=str)))

    @app.get("/api/runs/{run_id}/unit/{name}")
    def api_run_unit(name: str, run: dict[str, Any] = Depends(readable_run)) -> JSONResponse:
        d = unit_detail(board(run["id"]), name)
        if d is None:
            raise HTTPException(404, f"unknown unit {name}")
        return JSONResponse(json.loads(json.dumps(d, default=str)))

    @app.get("/api/runs/{run_id}/events")
    def api_run_events(after: int = 0, limit: int = 2000, run: dict[str, Any] = Depends(readable_run)):
        return JSONResponse(board(run["id"]).events_after(after, min(max(limit, 1), 5000)))

    @app.get("/api/runs/{run_id}/stream")
    async def api_run_stream(request: Request, after: int = 0,
                             run: dict[str, Any] = Depends(readable_run)) -> StreamingResponse:
        return StreamingResponse(event_stream(board(run["id"]), after, request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/report")
    def api_run_report(run: dict[str, Any] = Depends(readable_run)) -> PlainTextResponse:
        md = runs.out_dir(run["id"]) / "REPORT.md"
        if not md.exists():
            raise HTTPException(404, "no report yet")
        text = md.read_text()
        for root in sorted({str(runs.run_dir(run["id"]).resolve()), str(runs.run_dir(run["id"])), str(home)},
                           key=len, reverse=True):
            text = text.replace(root + "/", "").replace(root, ".")    # never show server paths
        return PlainTextResponse(text, media_type="text/markdown")

    @app.get("/api/runs/{run_id}/download")
    def api_run_download(run: dict[str, Any] = Depends(readable_run)) -> Response:
        out = runs.out_dir(run["id"])
        if not (out / "REPORT.md").exists():
            raise HTTPException(404, "nothing to download yet")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(out.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    zf.write(p, p.relative_to(out).as_posix())
        name = f"legacybridge-{run['id']}.zip"
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/specimen/refresh")
    async def api_specimen_refresh(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        """Re-run the public specimen (signed-in users only, never while it is running)."""
        if runs.is_active(SPECIMEN):
            raise HTTPException(409, "The specimen is running right now.")
        store.update_run(SPECIMEN, status="failed")   # forces ensure_specimen to rebuild it
        b = boards.pop(SPECIMEN, None)
        if b:
            b.close()
        ensure_specimen()
        return _public_run(store.run(SPECIMEN))  # type: ignore[arg-type]

    # ------------------------------------------------------------ page
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/app")
    def product_page() -> FileResponse:
        return FileResponse(STATIC / "app.html")

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    return app


def _toolchain() -> tuple[bool, str]:
    if shutil.which("cobc"):
        return True, "GnuCOBOL found"
    return False, "GnuCOBOL (cobc) is not installed: brew install gnucobol (macOS) / sudo apt-get install gnucobol"


async def wait_for_run(app: FastAPI, run_id: str, timeout: float = 120) -> None:
    """Test helper: wait until a run's child process has finished."""
    await asyncio.wait_for(app.state.runs.wait(run_id), timeout)
