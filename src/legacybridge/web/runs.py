"""Runs started from the website: each one is a real `legacybridge migrate` in a child process.

Layout per run: `<home>/runs/<id>/{src/, state/state.db, out/, run.log}`. The child process does all
the work (and itself runs legacy/generated code only through the sandbox); the web server only
reads the run's blackboard, exactly like the dashboard does.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, config_dir
from ..db import Blackboard
from .store import WebStore

SOURCE_EXT = {".cbl", ".cob", ".cobol"}
COPY_EXT = {".cpy", ".copy"}
MAX_FILES = 80
MAX_BYTES = 2 * 1024 * 1024
LOG_TAIL = 4000
PKG_ROOT = Path(__file__).resolve().parents[2]      # .../src, so the child imports this checkout


class UploadError(ValueError):
    """Rejected upload (names, types, sizes)."""


@dataclass
class Upload:
    name: str
    data: bytes


def live_keys_available(settings: Settings) -> list[str]:
    """Providers (enabled in providers.yaml) whose API key env var is set. Never returns key values."""
    return sorted(name for name, p in settings.providers.providers.items()
                  if p.enabled and os.environ.get(p.api_key_env))


def place_uploads(files: list[Upload], src: Path) -> dict[str, int]:
    """Validate and write uploaded files into a fresh corpus dir. Returns counts per kind."""
    if not files:
        raise UploadError("No files.")
    if len(files) > MAX_FILES:
        raise UploadError(f"At most {MAX_FILES} files.")
    if sum(len(f.data) for f in files) > MAX_BYTES:
        raise UploadError(f"At most {MAX_BYTES // 1024 // 1024} MB in total.")
    plan: list[tuple[Path, bytes]] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for f in files:
        name = Path(f.name.replace("\\", "/")).name          # never trust client paths
        stem, ext = Path(name).stem, Path(name).suffix.lower()
        if not stem or not all(c.isalnum() or c in "-_" for c in stem):
            raise UploadError(f"Bad file name: {f.name!r} (letters, digits, - and _ only).")
        if name.lower() in seen:
            raise UploadError(f"Duplicate file: {name}")
        seen.add(name.lower())
        try:
            text = f.data.decode("utf-8")
        except UnicodeDecodeError:
            raise UploadError(f"{name}: not UTF-8 text.") from None
        if "\x00" in text:
            raise UploadError(f"{name}: binary content.")
        if ext in SOURCE_EXT:
            sub, kind = "src", "programs"
        elif ext in COPY_EXT:
            sub, kind = "copybooks", "copybooks"
        elif ext == ".txt":
            sub, kind = "scenarios", "scenarios"   # <PROGRAM>-<name>.txt, replayed end to end
        else:
            raise UploadError(f"{name}: expected .cbl/.cob/.cobol, .cpy/.copy or scenario .txt files.")
        plan.append((src / sub / name, text.encode()))
        counts[kind] += 1
    if not counts["programs"]:
        raise UploadError("Add at least one COBOL program (.cbl / .cob).")
    for path, data in plan:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return dict(counts)


def run_summary(state_db: Path) -> dict[str, Any]:
    """Counts from a finished (or running) run's blackboard, for the ledger."""
    if not state_db.exists():
        return {}
    db = Blackboard(state_db)
    try:
        mods = {m["name"]: m for m in db.modules()}
        units = [u for u in db.units() if any((mods.get(m) or {}).get("kind") != "copybook" for m in u["members"])]
        counts = Counter(u["status"] for u in units)
        scen = db.get_meta("scenarios", []) or []
        tests = db.one("SELECT COUNT(*) n FROM test_cases WHERE origin != 'hidden'")
        return {
            "units": len(units), "verified": counts.get("verified", 0),
            "blocked": counts.get("blocked", 0) + counts.get("dep_blocked", 0),
            "modules": sum(1 for m in mods.values() if m["kind"] != "copybook"),
            "tests": tests["n"] if tests else 0,
            "fuzz_bugs": sum(u["fuzz_bugs_found"] or 0 for u in units),
            "attempts": sum(u["attempts"] or 0 for u in units),
            "scenarios": len(scen), "scenarios_identical": sum(1 for s in scen if s.get("passed") is True),
            "package": bool(db.get_meta("package_dir")),
        }
    finally:
        db.close()


class RunManager:
    """Prepares run directories and supervises the child `migrate` processes."""

    def __init__(self, home: Path, store: WebStore, settings: Settings, demo_root: Path, max_parallel: int = 2):
        self.home = home.resolve()
        self.store = store
        self.settings = settings
        self.demo_root = demo_root.resolve()
        self.max_parallel = max_parallel
        self._sem: asyncio.Semaphore | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    # ------------------------------------------------------------ paths
    def run_dir(self, run_id: str) -> Path:
        return self.home / "runs" / run_id

    def state_db(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state" / "state.db"

    def out_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "out"

    # ------------------------------------------------------------ preparing sources
    def prepare_demo(self, run_id: str) -> None:
        """Copy the demo corpus (reference_py included: the offline MockProvider replays it)."""
        dst = self.run_dir(run_id) / "src"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(self.demo_root, dst, ignore=shutil.ignore_patterns("__pycache__", ".*"))

    def prepare_upload(self, run_id: str, files: list[Upload]) -> dict[str, int]:
        dst = self.run_dir(run_id) / "src"
        try:
            return place_uploads(files, dst)
        except UploadError:
            shutil.rmtree(self.run_dir(run_id), ignore_errors=True)
            raise

    # ------------------------------------------------------------ supervising
    def command(self, run: dict[str, Any]) -> list[str]:
        rd = self.run_dir(run["id"])
        cmd = [sys.executable, "-m", "legacybridge.cli", "migrate", str(rd / "src"),
               "--state", str(rd / "state"), "--out", str(rd / "out"), "--quiet"]
        if run["dry_run"]:
            cmd.append("--dry-run")
        if not run["fuzz"]:
            cmd.append("--no-fuzz")
        return cmd

    def start(self, run: dict[str, Any]) -> None:
        """Schedule the run (at most `max_parallel` at a time)."""
        self._tasks[run["id"]] = asyncio.create_task(self._supervise(run))

    def is_active(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        return t is not None and not t.done()

    async def wait(self, run_id: str) -> None:
        t = self._tasks.get(run_id)
        if t is not None:
            await asyncio.shield(t)

    async def _supervise(self, run: dict[str, Any]) -> None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_parallel)
        rid = run["id"]
        rd = self.run_dir(rid)
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(PKG_ROOT), os.environ.get("PYTHONPATH")])),
               "LEGACYBRIDGE_CONFIG_DIR": str(config_dir().resolve()), "PYTHONUNBUFFERED": "1"}
        async with self._sem:
            self.store.update_run(rid, status="running", started_at=time.time())
            log = rd / "run.log"
            code: int | None = None
            try:
                with log.open("wb") as fh:
                    proc = await asyncio.create_subprocess_exec(*self.command(run), stdout=fh, stderr=fh, env=env,
                                                                cwd=str(Path.cwd()))
                    self._procs[rid] = proc
                    code = await proc.wait()
            except Exception as exc:  # noqa: BLE001 - a run must never take the server down
                with log.open("a") as fh:
                    fh.write(f"\n[web] could not start migrate: {exc!r}\n")
            finally:
                self._procs.pop(rid, None)
            summary = await asyncio.to_thread(run_summary, self.state_db(rid))
            # migrate exits 0 (all verified, scenarios identical) or 1 (some unit/scenario not); anything
            # else (2: no keys / bad corpus, crash, signal) is a failed run
            ok = code in (0, 1) and bool(summary.get("units"))
            note = None if ok else _tail(log)
            self.store.update_run(rid, status="done" if ok else "failed", exit_code=code, note=note,
                                  finished_at=time.time(), summary=summary)

    async def shutdown(self) -> None:
        """Stop child processes (server exit)."""
        for proc in list(self._procs.values()):
            if proc.returncode is None:
                proc.terminate()
        for t in list(self._tasks.values()):
            if not t.done():
                try:
                    await asyncio.wait_for(asyncio.shield(t), 5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    t.cancel()

    def recover(self) -> None:
        """Mark runs a previous server left queued/running as interrupted (their process is gone)."""
        for run in self.store.interrupted_runs():
            self.store.update_run(run["id"], status="failed", note="interrupted: the server stopped during this run",
                                  finished_at=time.time(), summary=run_summary(self.state_db(run["id"])))

    def remove(self, run_id: str) -> None:
        shutil.rmtree(self.run_dir(run_id), ignore_errors=True)
        self.store.delete_run(run_id)


def _tail(path: Path) -> str:
    try:
        data = path.read_text(errors="replace")
    except OSError:
        return ""
    return data[-LOG_TAIL:].strip()
