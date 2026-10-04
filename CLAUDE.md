# LegacyBridge — project rules for Claude

LegacyBridge migrates legacy code (v1: COBOL -> Python 3) with LLM agents and only accepts a
migrated module once differential testing against the ORIGINAL program (compiled with GnuCOBOL)
shows identical behavior. The LLM never decides a verdict.

## Commands

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # setup
.venv/bin/pytest -q                                             # all tests (offline, MockProvider)
.venv/bin/pytest -q -m "not cobc"                               # tests that don't need GnuCOBOL
.venv/bin/legacybridge analyze examples/bank_demo
.venv/bin/legacybridge migrate examples/bank_demo --out out/ --dry-run   # offline, mock LLM
.venv/bin/legacybridge migrate examples/bank_demo --out out/             # real free-tier APIs
.venv/bin/legacybridge dashboard                                 # http://127.0.0.1:8765
.venv/bin/legacybridge eval --dry-run
```

macOS quirk: iCloud (Desktop) sets the `hidden` flag on synced files and Python 3.13 skips hidden
`.pth` files, which breaks the editable install. The venv therefore lives in `.venv.nosync/`
(iCloud skips `*.nosync`) with a `.venv` symlink to it. If it breaks again:
`chflags -R nohidden .venv.nosync`.

GnuCOBOL: `brew install gnucobol` (macOS) / `apt-get install gnucobol` (Debian/Ubuntu).

## Rules

- Python 3.11+, asyncio for concurrency, type hints, docstrings on public functions. Small modules.
- SQLite blackboard (`src/legacybridge/db.py`) is the single source of truth. Agents never call
  each other; they read/write the blackboard. Every state change emits a row in `events`.
- Legacy and generated code run ONLY via `legacybridge.sandbox` (subprocess, timeout, rlimits,
  scrubbed env, macOS seatbelt network deny when available). Never `exec`/`import` generated code
  in the main process.
- Only free hosted LLM APIs. Models/limits live in `config/providers.yaml`, never in code.
  API keys only from env vars (`.env`, see `.env.example`). Never log keys.
- Everything must run offline with `MockProvider` (`--dry-run`, and all tests).
- Language-specific logic lives behind `adapters/base.py::LanguageAdapter`. Only
  `adapters/cobol_python` is implemented.
- Verdicts are deterministic (`agents/verifier.py`): exact match by default, normalization only via
  `config/pipeline.yaml`.
- Record non-obvious decisions in `docs/ASSUMPTIONS.md`.
- Don't move on with failing tests. Commit per phase.

## Architecture (short)

```
analyze -> graph(SCC units) -> scheduler (async work queue, resume from SQLite)
  per unit:  oracle (LLM inputs + property/random inputs -> run COBOL -> golden)
             translate -> verify -> repair loop (escalating models) -> fuzz K rounds -> VERIFIED
             reflector stores lessons on successful repair
integrator -> runnable Python package + end-to-end scenario diff + report
dashboard  -> FastAPI + SSE over the events table
```

Key paths: `src/legacybridge/{cli,config,db,graph,scheduler,sandbox,pipeline}.py`,
`llm/` (client, ratelimit, cache, providers, mock), `agents/`, `adapters/cobol_python/`
(parser, cobc runner, driver generator, harness generator, runtime `lbrt.py`, idiom guide),
`dashboard/`, `evaluation/`. Demo corpus: `examples/bank_demo/`.
`examples/bank_demo/reference_py/` holds hand-written reference translations used ONLY by the
MockProvider so the offline pipeline is exercisable; real runs never read them.
