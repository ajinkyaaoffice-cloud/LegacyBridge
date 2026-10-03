# Assumptions and decisions

Decisions made autonomously while building LegacyBridge. Each has a short rationale.

## Environment
- **Project directory** renamed from `VisionDocRAG` to `legacybridge` at the user's request.
- **GnuCOBOL 3.2** was installed with `brew install gnucobol` because differential testing against
  real COBOL output is the core of the project. Other platforms: `apt-get install gnucobol`.
- **Python 3.13** venv (requirement is 3.11+). `enum.StrEnum` is used, so 3.11 is the floor.

## Free LLM providers (checked 2026-10-03)
- Gemini (AI Studio free tier): `gemini-3.5-flash` / `gemini-3.1-flash-lite`. Google no longer
  publishes per-model free limits on the docs page (they're shown in AI Studio), so limits in
  `providers.yaml` are conservative estimates (10 RPM / 250 RPD for flash).
- Groq free plan: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` at
  30 RPM / 8K TPM / 1K RPD / 200K TPD (from console.groq.com/docs/rate-limits). The 8K TPM is
  smaller than a full translation prompt for big modules; the context budgeter skips a model
  whose TPM can't fit the request and moves down the chain.
- OpenRouter `:free` models: 20 RPM, 50 requests/day with a $0 balance (shared across models,
  modelled as `shared_limits`).
- Mistral "Experiment" plan: free with phone verification; exact limits only in the console.
  Modelled as 30 RPM shared.
- Cerebras: the always-free tier became a card-gated $5 trial in 2026-07 — this is not "free"
  under the brief, so it is present but `enabled: false`.

## Sandbox
- Process group + wall timeout + RLIMIT_CPU/FSIZE/CORE everywhere; RLIMIT_AS only on Linux
  (macOS doesn't enforce it and setting it can break exec).
- On macOS, `sandbox-exec` (seatbelt) denies all network access and all file writes outside the
  per-run work directory. It's deprecated by Apple but still functional; it's skipped
  automatically where unavailable. On Linux, run inside a container for network isolation.
- Child environment is rebuilt from scratch (PATH/HOME/LANG only), so API keys never reach
  legacy or generated code.

## GnuCOBOL semantics (verified empirically with cobc 3.2, see tests/test_lbrt.py)
- `DISPLAY numeric-item` prints a sign whenever the field is signed and inserts '.' for implied
  decimals: S9(7)V99 165.28 -> `+0000165.28`; 9(5) 3 -> `00003`. COMP/COMP-3 display the same way.
- Storing truncates decimals and silently drops high-order digits unless ON SIZE ERROR is coded;
  `ROUNDED` = round half away from zero (`ROUND_HALF_UP` in Python's decimal).
- LINE SEQUENTIAL input: short/empty lines are space-padded, trailing CR stripped. Lines longer
  than the record are split into two records and one character is lost (!), so the oracle
  rejects test inputs whose lines exceed the record length.
- `IS NUMERIC` on SIGN LEADING SEPARATE requires an explicit '+' or '-'.
- Known lbrt divergence: 1000 MOVEd to `+ZZ9` gives `+000` in GnuCOBOL but `+  0` in lbrt
  (overflow into a fixed leading '+' picture). Not used by the corpus; the verifier would catch it.

## Environment quirk
- On macOS, files under ~/Desktop may get the `hidden` flag (iCloud). Python 3.13 ignores hidden
  `.pth` files, breaking editable installs. Fix: `chflags nohidden .venv/lib/python3.*/site-packages/*.pth`.
  pytest is configured with `pythonpath = ["src", "tests"]` so tests don't depend on it.

## Differential-testing contract (Phase 3)
- **Subprograms** are tested through a generated COBOL driver (`adapters/cobol_python/driver.py`):
  one stdin line = one CALL, every elementary parameter field `|`-separated (numerics: digits
  with implied decimals, `+`/`-` prefix when signed). The driver re-declares the LINKAGE records
  from the parsed data tree (free format) instead of COPYing them, so it also works for
  parameters not defined in copybooks. Output-only fields are still supplied as input: some
  programs deliberately leave outputs untouched on error, and that must be captured.
- All lines of one test case run in ONE process, so WORKING-STORAGE persistence across CALLs is
  part of the observed behavior (the Python harness must preserve module state the same way).
- **Mains** read their LINE SEQUENTIAL input file from stdin (`ASSIGN TO KEYBOARD`).
- Inputs are rejected (not recorded) if lines exceed the record length (GnuCOBOL would split them
  and lose a character), contain non-printable characters, or, for driver lines, have malformed
  numerics (driver input is not what we're testing).
- LLMs propose cases as **field values** (JSON), never as fixed-width text: the adapter formats
  them. LLMs are bad at counting columns; this made nearly all proposed cases usable.
- The oracle runs each module twice on one input first and refuses non-deterministic programs.
- Compile cache key = cobc version + all involved sources + copybooks + driver text.
- Input generators harvest literals from the PROCEDURE DIVISION (numbers, and string literals a
  field is compared to in IF/EVALUATE) as boundary candidates; IDs introduced by a leading record
  are reused by following records so transactions hit existing accounts.
- `hidden` test cases (eval only) are generated with a different seed and never shown to the
  translator, verifier, repair loop or fuzzer.
