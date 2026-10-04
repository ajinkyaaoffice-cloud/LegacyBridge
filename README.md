# LegacyBridge

**Verified legacy-code migration with LLM agents.** v1 migrates COBOL to Python 3.

LLMs translate code. LegacyBridge decides whether a translation is right without asking an LLM.
Every module is compiled from the **original** COBOL source with GnuCOBOL and run on generated
inputs; the Python translation must reproduce its output byte for byte. Failures go back to the
model as structured diffs. An adversarial fuzzer then hunts for inputs where the two still
diverge. A module is accepted only when both stages pass.

```
analyze → SCC units → async scheduler ─┬─ oracle: LLM + boundary + random inputs → run original → golden tests
                                       ├─ translate → verify (exact diff) → repair loop (escalating models)
                                       ├─ fuzz: inputs aimed at the current code, until K clean rounds
                                       └─ reflector: lessons for later units
integrator → runnable Python package + end-to-end scenario diff + REPORT.md        dashboard: live graph
```

- **The LLM never decides a verdict.** Verdicts are an exact comparison of stdout and exit code
  between the original and the translation, both run in a sandbox (subprocess, timeout, rlimits,
  scrubbed env, no network on macOS).
- **Only free hosted LLM APIs** are used: Gemini, Groq, OpenRouter `:free` models, Mistral.
  Rate-limit-aware fallback chains are configured in `config/providers.yaml`.
- **Fully offline mode.** `--dry-run` and the whole test suite use a deterministic MockProvider.
- **Blackboard architecture.** SQLite holds all state: resumable runs, a live dashboard, and an
  auditable record of every attempt, test and verdict.

## Quick start

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev]'
brew install gnucobol            # macOS   (Debian/Ubuntu: sudo apt-get install gnucobol)
```

```bash
.venv/bin/legacybridge analyze examples/bank_demo
```

```bash
.venv/bin/legacybridge migrate examples/bank_demo --out out/ --dry-run
```

The dry run is offline and takes about 15 s. It migrates the 11-program demo bank system. To
watch it live, start the dashboard first in another terminal:

```bash
.venv/bin/legacybridge dashboard
```

It serves http://127.0.0.1:8765. Then run the migration. For a real run, put your keys in `.env`
(see below) and drop `--dry-run`:

```bash
.venv/bin/legacybridge migrate examples/bank_demo --out out/
```

The migrated package runs like the original:

```bash
cd out && python3 -m bank_demo_py INTPOST < ../examples/bank_demo/scenarios/INTPOST-month_end.txt
```

> **macOS note.** If this repo lives under an iCloud-synced folder (e.g. `~/Desktop`), files
> inside `.venv` get the `hidden` flag. Python 3.13 then skips the editable-install `.pth` file,
> and `import legacybridge` fails. The test suite is unaffected (pytest sets `pythonpath`). For
> scripts or `python -m`, use `PYTHONPATH=src`, for example
> `PYTHONPATH=src .venv/bin/python -m legacybridge.cli migrate …`. Alternatively, clear the flag
> with `chflags -R nohidden .venv` (it may come back after the next sync), or keep the repo
> outside iCloud.

## API keys (all free tiers)

Copy `.env.example` to `.env` and fill in whichever keys you have. Providers without a key are
skipped, and one key is enough to run.

| env var | where | notes |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | no card; Flash / Flash-Lite |
| `GROQ_API_KEY` | https://console.groq.com/keys | fast; small TPM, so big prompts fall through the chain |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys | `:free` models only (about 50 requests/day at a $0 balance) |
| `MISTRAL_API_KEY` | https://console.mistral.ai/api-keys | free "Experiment" plan (phone verification); Codestral |

Keys are read only from the environment and are never logged. Model names, rate limits, fallback
tiers and role-to-tier mapping all live in `config/providers.yaml`; edit that file when free-tier
limits change.

## Commands

| command | what it does |
|---|---|
| `analyze PATH` | Parse the corpus and show modules, the call/copy graph, SCC units and migration waves (`--summaries` adds LLM summaries). |
| `migrate PATH [--out DIR] [-j N] [--dry-run] [--resume [--retry-blocked]] [--no-fuzz]` | Run the full pipeline. With `--out`, also write the package, replay the scenarios and write `REPORT.md` / `report.json`. Exits non-zero unless every unit is verified and every scenario is identical. |
| `verify PATH [--hidden]` | Re-run every stored translation against all recorded golden tests. |
| `report PATH [--out DIR] [--no-scenarios]` | Re-package, replay the scenarios and write the report for the last run. |
| `dashboard [--port 8765]` | Live view: unit graph by status, per-unit attempts with counterexamples and code, live events, lessons, LLM usage, scenarios. |
| `web [--port 8766] [--no-specimen]` | The website: the story at `/`, the product at `/app` (sign in with a punch card, or Supabase e-mail/GitHub when configured; feed COBOL by upload, the demo deck or a GitHub repo cloned into the browser; watch the graph, inspect attempts, ship the package). Runs are offline unless the server has keys. |
| `eval [PATH] [--dry-run]` | Baseline A (single-shot) vs Baseline B (tests + repair) vs the full pipeline, scored on a held-out suite. See [docs/EVAL.md](docs/EVAL.md). |

State lives in `.legacybridge/` (change it with `--state DIR`). The LLM response cache lives in
`.lbcache/`.

## Evaluation (dry run)

| | A: single-shot | B: tests + repair | Full |
|---|---|---|---|
| Modules correct on held-out tests | 6/11 | 10/11 | **11/11** |
| VERIFIED but actually wrong | — (ungated) | **1** | **0** |
| End-to-end scenarios identical | 2/4 | 3/4 | **4/4** |
| LLM calls | 21 | 26 | 54 |

The dry run measures the pipeline machinery against a catalogue of realistic bugs; it is not a
benchmark of model quality. Its main finding is that a test-based gate on its own still accepted
one wrong module (an off-by-one page break that the oracle's short inputs never trigger).
Adversarial fuzzing found and repaired it. Details are in [docs/EVAL.md](docs/EVAL.md).

## The demo corpus

`examples/bank_demo/` is a small batch banking system: 11 programs and 8 copybooks. It includes a
CALL cycle (ACCTVAL ↔ LOANCHK, migrated as one unit), COMP-3, ROUNDED vs truncation, edited
pictures, REDEFINES / record overlays, date arithmetic, STRING and page-break logic, plus
realistic end-to-end scenario files in `scenarios/`. `reference_py/` holds hand-written
translations that verify against GnuCOBOL. Only the MockProvider uses them, so the offline
pipeline has something to exercise.

## Development

```bash
.venv/bin/pytest -q
```

```bash
.venv/bin/pytest -q -m "not cobc"
```

The first command runs all tests offline in about 2 minutes. The second skips tests that need
GnuCOBOL.

Docs:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, test protocol, acceptance rules,
  offline mode, adding a language pair.
- [docs/EVAL.md](docs/EVAL.md): evaluation method and results.
- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md): every non-obvious decision.
- [docs/PLAN.md](docs/PLAN.md): build phases.
