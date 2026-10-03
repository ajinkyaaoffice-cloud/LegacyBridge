# Evaluation

```bash
.venv/bin/legacybridge eval --dry-run          # offline, MockProvider (deterministic)
.venv/bin/legacybridge eval                    # live, free-tier APIs (needs keys in .env)
```

Results are written to `eval/results/`:

- `latest-<mode>.{md,json}`, the most recent run;
- a timestamped copy of each run.

Scratch state goes to `eval/runs/` (ignored by git).

## Question

How much does each part of the pipeline matter? In particular, do the verification gate, the
repair loop and adversarial fuzzing actually stop wrong code from shipping, or do they only
cost tokens?

## Configurations

All three use the same prompts, models, corpus and oracle. Only the feedback machinery
differs (`PipelineOptions`).

| config | repair loop | fuzzing | lessons | gate |
|---|---|---|---|---|
| **A**: single-shot | no (1 attempt) | no | no | no: the translation is shipped whatever the tests say |
| **B**: tests + repair | yes (≤4 attempts, escalating) | no | no | yes |
| **Full** | yes | yes (K=2 clean rounds) | yes | yes |

A's prompt still contains sample I/O pairs, so the prompts are identical across configs and the
comparison isolates the feedback loops. A plain "translate this file" baseline would be weaker
still.

## Scoring: a held-out suite, and no LLM

The scorer needs test cases the pipeline never saw:

- They are generated once per eval run from an **independent seed** (`HIDDEN_SEED`). Up to 60 per
  module: one third property/boundary cases, one third random, one third **volume** cases. Volume
  cases are long files with one header and many detail records, or many CALLs in a row. The
  oracle generator never produces them.
- Expected outputs are recorded from the **original** COBOL programs.
- For each configuration, any hidden input that the configuration saw during migration (as an
  oracle or fuzz test) is dropped from its score.
- Every accepted module is run on its held-out tests, using the code the configuration actually
  accepted together with that configuration's dependencies. The end-to-end scenarios are replayed
  on the assembled package.

Metrics:

- **correct on hidden suite**: a module is accepted and all of its held-out tests pass.
- **accepted but wrong**: code shipped with a divergence.
- **VERIFIED but wrong**, the false-accept rate of the gate. This is the number that matters for
  a migration tool.
- **end-to-end scenarios identical**.
- **attempts, fuzz counterexamples, lessons, LLM calls and tokens, wall time**: the cost side.

## Results: dry run (MockProvider), bank_demo, 11 modules / 10 executable units

From `eval/results/latest-dryrun.md`:

| metric | A: single-shot | B: tests + repair | Full |
|---|---|---|---|
| Modules correct on hidden suite | 6/11 | 10/11 | **11/11** |
| Hidden-test pass rate | 73.2% | 97.0% | **100.0%** |
| Modules accepted, of which wrong | 11, **5 wrong** | 11, 1 wrong | 11, 0 wrong |
| VERIFIED but wrong (false accept) | 0 | **1** | **0** |
| End-to-end scenarios identical | 2/4 | 3/4 | **4/4** |
| Translation/repair attempts | 10 | 15 | 16 |
| LLM calls | 21 | 26 | 54 |
| Tokens in / out | 78.5k / 29.4k | 112.6k / 35.1k | 213.7k / 99.8k |

**How to read these numbers.** In a dry run the "LLM" replays reference translations with a
fixed catalogue of bugs (see [ARCHITECTURE.md](ARCHITECTURE.md#offline-mode)). The numbers
therefore show that the machinery behaves as designed. They are **not** a measure of model quality.

- **A** ships all four buggy modules. The fifth wrong module is INTPOST: its own code is correct,
  but it calls the buggy INTCALC. Nothing stops the bugs, because A has no gate.
- **B** fixes everything the oracle tests expose, including the BALUPD record-overlay bug, which
  needs the escalated tier. However, STMTRPT's page-break bug (counter compared at 11 instead of
  10) never triggers on the oracle's short inputs. B marks STMTRPT VERIFIED, and it is wrong on
  long statements: a **false accept**. This is the core risk of test-based acceptance: the
  verdict is only as good as the tests.
- **Full** closes that gap. In fuzz round 1 the LLM is shown the current code and asks for a long
  statement. That input diverges from the original and becomes a golden test, and the repair loop
  fixes the bug. Lessons from earlier units also stop BALUPD from repeating the trailing-spaces
  mistake. The cost is about 2× the LLM calls of B, mostly fuzz-round input generation, which runs
  on the cheap "fast" tier.

## Live runs

With keys in `.env`, `legacybridge eval` runs the same protocol against the free-tier chains in
`config/providers.yaml`. Expect the following:

- **Rate limits dominate wall time.** Free tiers allow 10–30 RPM, and some only tens of requests
  per day. The client waits for token-bucket slots and falls back along the chain. Use `-j 1`–`2`.
- **The three configs share the on-disk LLM cache**, keyed by model and prompt. Wherever the
  prompts are identical (oracle inputs; first translations of units that get no lessons), B and
  Full re-use A's responses, which keeps the comparison paired. Cached hits are reported
  separately.
- **Repeat for variance.** Results vary between runs as models and free-tier routing change. To
  estimate variance, run the eval several times with `cache_dir` pointed at a fresh directory each
  time.
