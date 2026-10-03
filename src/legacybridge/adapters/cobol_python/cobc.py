"""Build and run the ORIGINAL COBOL with GnuCOBOL, inside the sandbox.

Executables are cached by a content hash of every source involved (program, transitive callees,
copybooks, generated driver) plus the cobc version, so re-runs and resumes don't recompile.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from ...sandbox import RunResult, Sandbox


class CompileError(RuntimeError):
    """cobc rejected the program (or its generated driver)."""


def cobc_version(cobc: str) -> str:
    try:
        out = subprocess.run([cobc, "--version"], capture_output=True, text=True, timeout=20).stdout
        return out.splitlines()[0] if out else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class CobcRunner:
    """Compile cache + sandboxed execution of legacy executables."""

    def __init__(self, cobc: str, sandbox: Sandbox, work_dir: Path, copy_dirs: list[Path]):
        self.cobc = cobc
        self.sandbox = sandbox
        self.work_dir = Path(work_dir)
        self.copy_dirs = copy_dirs
        self.version = cobc_version(cobc)
        self._locks: dict[str, asyncio.Lock] = {}

    def _key(self, sources: list[Path], driver: str | None, copybooks: list[Path]) -> str:
        h = hashlib.sha256(self.version.encode())
        for p in sorted(sources) + sorted(copybooks):
            h.update(p.name.encode() + b"\0" + p.read_bytes())
        if driver:
            h.update(b"driver\0" + driver.encode())
        return h.hexdigest()[:20]

    async def build(self, sources: list[Path], driver: str | None, copybooks: list[Path]) -> Path:
        """Return the cached executable for `sources` (+ driver as main program), compiling if needed."""
        key = self._key(sources, driver, copybooks)
        out_dir = self.work_dir / "bin" / key
        exe = out_dir / "prog"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if exe.exists():
                return exe
            build_dir = Path(tempfile.mkdtemp(prefix="build-", dir=self._ensure(self.work_dir / "bin")))
            try:
                argv = [self.cobc, "-x", "-o", str(build_dir / "prog")]
                if driver:
                    (build_dir / "lbdriver.cbl").write_text(driver)
                    argv.append(str(build_dir / "lbdriver.cbl"))
                argv += [str(p.resolve()) for p in sources]
                for d in self.copy_dirs:
                    argv += ["-I", str(d.resolve())]
                res = await self.sandbox.run(argv, cwd=build_dir, timeout=240, cpu_seconds=180)
                if res.exit_code != 0 or not (build_dir / "prog").exists():
                    raise CompileError(_clean_stderr(res.stderr) or res.stdout or "cobc failed")
                if out_dir.exists():
                    shutil.rmtree(out_dir, ignore_errors=True)
                build_dir.rename(out_dir)
            finally:
                if build_dir.exists():
                    shutil.rmtree(build_dir, ignore_errors=True)
        return exe

    async def run(self, exe: Path, stdin: str, timeout: float | None = None) -> RunResult:
        """Run a legacy executable in a fresh sandbox directory."""
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=self._ensure(self.work_dir / "runs")))
        try:
            return await self.sandbox.run([str(exe)], stdin=stdin, cwd=run_dir, timeout=timeout)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @staticmethod
    def _ensure(p: Path) -> Path:
        p.mkdir(parents=True, exist_ok=True)
        return p


def _clean_stderr(text: str) -> str:
    """Drop macOS linker noise and cobc's free-format listing notes."""
    keep = [ln for ln in text.splitlines()
            if "ld: warning" not in ln and "free format detected" not in ln and not ln.lstrip().startswith(("1 >", "2 |", "3 |"))]
    return "\n".join(keep).strip()
