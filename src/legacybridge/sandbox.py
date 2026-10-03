"""Subprocess sandbox for running legacy binaries and generated Python.

Nothing legacy or generated is ever executed in the main process. Each run gets:
- its own process group (killed as a whole on timeout),
- wall-clock timeout + RLIMIT_CPU / RLIMIT_AS / RLIMIT_FSIZE / no core dumps,
- a scrubbed environment (no API keys leak into child processes),
- on macOS, a seatbelt profile (sandbox-exec) denying network and writes outside its work dir,
- capped stdout/stderr capture.
"""

from __future__ import annotations

import asyncio
import os
import resource
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import SandboxCfg

_SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"


@dataclass
class RunResult:
    """Outcome of one sandboxed process."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: float
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0


def _seatbelt_profile(workdir: Path) -> str:
    wd = str(workdir.resolve())
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{wd}") (literal "/dev/null") (literal "/dev/stdout")
       (literal "/dev/stderr") (literal "/dev/tty") (regex #"^/dev/fd/"))
"""


class Sandbox:
    """Runs commands in isolated subprocesses with resource limits. Safe to share across tasks."""

    def __init__(self, cfg: SandboxCfg | None = None):
        self.cfg = cfg or SandboxCfg()
        self._sem = asyncio.Semaphore(self.cfg.parallelism)
        self.seatbelt = bool(
            self.cfg.macos_seatbelt and sys.platform == "darwin" and shutil.which("sandbox-exec")
        )

    def _limits(self) -> None:  # runs in the child between fork and exec
        os.setsid()
        cpu = self.cfg.cpu_seconds
        _try_rlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        _try_rlimit(resource.RLIMIT_CORE, (0, 0))
        _try_rlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
        if sys.platform != "darwin":  # RLIMIT_AS is not enforced (and can break exec) on macOS
            mem = self.cfg.memory_mb * 1024 * 1024
            _try_rlimit(resource.RLIMIT_AS, (mem, mem))

    async def run(
        self,
        argv: list[str],
        *,
        stdin: str = "",
        cwd: Path,
        env_extra: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> RunResult:
        """Run `argv` in `cwd` feeding `stdin`; never raises for child failures."""
        cwd = Path(cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": _SAFE_PATH,
            "HOME": str(cwd),
            "TMPDIR": str(cwd),
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
        }
        env.update(env_extra or {})
        cmd = list(argv)
        if self.seatbelt:
            cmd = ["sandbox-exec", "-p", _seatbelt_profile(cwd), *cmd]
        timeout = timeout or self.cfg.timeout_s
        cap = self.cfg.max_output_bytes

        async with self._sem:
            start = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=self._limits,
            )
            truncated = False

            async def pump(stream: asyncio.StreamReader) -> bytes:
                nonlocal truncated
                buf = bytearray()
                while chunk := await stream.read(65536):
                    if len(buf) < cap:
                        buf.extend(chunk[: cap - len(buf)])
                    if len(buf) >= cap:
                        truncated = True
                        _kill(proc)
                return bytes(buf)

            async def feed() -> None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(stdin.encode())
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:  # pragma: no cover
                        pass

            assert proc.stdout is not None and proc.stderr is not None
            gather = asyncio.gather(pump(proc.stdout), pump(proc.stderr), feed(), proc.wait())
            timed_out = False
            try:
                out, err, _, _ = await asyncio.wait_for(gather, timeout)
            except asyncio.TimeoutError:
                timed_out = True
                _kill(proc)
                await proc.wait()
                out, err = b"", b"timeout"
            duration = (time.perf_counter() - start) * 1000

        code = proc.returncode if proc.returncode is not None else -9
        return RunResult(
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
            exit_code=code,
            timed_out=timed_out,
            duration_ms=duration,
            truncated=truncated,
        )

    async def run_python(self, script: Path, *, stdin: str = "", cwd: Path, args: list[str] | None = None,
                         timeout: float | None = None) -> RunResult:
        """Run a Python file in isolated mode (-I: no user site, no PYTHON* env, cwd not on path)."""
        return await self.run([sys.executable, "-I", "-B", str(script), *(args or [])], stdin=stdin, cwd=cwd,
                              timeout=timeout)


def _try_rlimit(which: int, value: tuple[int, int]) -> None:
    try:
        soft, hard = resource.getrlimit(which)
        if hard != resource.RLIM_INFINITY:
            value = (min(value[0], hard), min(value[1], hard))
        resource.setrlimit(which, value)
    except (ValueError, OSError):
        pass


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
