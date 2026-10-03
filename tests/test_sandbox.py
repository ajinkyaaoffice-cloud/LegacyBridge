import os
import sys

import pytest

from legacybridge.config import SandboxCfg
from legacybridge.sandbox import Sandbox


@pytest.fixture
def sb():
    return Sandbox(SandboxCfg(timeout_s=5, cpu_seconds=5, max_output_bytes=10_000))


async def test_stdin_stdout_roundtrip(sb, tmp_path):
    r = await sb.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper(), end='')"],
                     stdin="hello\n", cwd=tmp_path)
    assert r.ok and r.stdout == "HELLO\n"


async def test_timeout_kills(sb, tmp_path):
    r = await sb.run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout=0.5)
    assert r.timed_out and not r.ok


async def test_exit_code_and_stderr(sb, tmp_path):
    r = await sb.run([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"], cwd=tmp_path)
    assert r.exit_code == 3 and "boom" in r.stderr


async def test_env_is_scrubbed(sb, tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-should-not-leak")
    r = await sb.run([sys.executable, "-c", "import os; print(os.environ.get('GEMINI_API_KEY'))"], cwd=tmp_path)
    assert r.stdout.strip() == "None"


async def test_output_cap(sb, tmp_path):
    r = await sb.run([sys.executable, "-c", "while True: print('x'*1000)"], cwd=tmp_path)
    assert r.truncated and len(r.stdout) <= 10_000


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS only")
async def test_seatbelt_blocks_network_and_outside_writes(sb, tmp_path):
    if not sb.seatbelt:
        pytest.skip("sandbox-exec unavailable")
    code = (
        "import socket\n"
        "try:\n socket.create_connection(('1.1.1.1', 53), timeout=2); print('net-ok')\n"
        "except OSError: print('net-blocked')\n"
        "try:\n open(%r, 'w').write('x'); print('write-ok')\n"
        "except OSError: print('write-blocked')\n"
    ) % os.path.expanduser("~/lb_sandbox_probe.txt")
    r = await sb.run([sys.executable, "-c", code], cwd=tmp_path)
    assert "net-blocked" in r.stdout and "write-blocked" in r.stdout
    inside = await sb.run([sys.executable, "-c", "open('ok.txt','w').write('y'); print('in-ok')"], cwd=tmp_path)
    assert "in-ok" in inside.stdout
