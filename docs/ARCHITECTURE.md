# Architecture

LegacyBridge migrates a legacy code base module by module with LLM agents. A module is accepted
only when **differential testing against the original program** shows identical behaviour. The
original is compiled with GnuCOBOL. The LLM proposes code and test inputs. It never decides a
verdict.

```
                 ┌──────────── SQLite blackboard (state.db, WAL) ────────────┐
                 │ modules · units · edges · test_cases · attempts ·          │
                 │ verification_results · lessons · llm_calls · events        │
                 └──▲──────────▲───────────▲──────────▲──────────▲───────────┘
                    │          │           │          │          │ (read only)
 analyze ──► graph (SCC units, waves) ──► scheduler ──► per unit pipeline ──► integrator ──► package + REPORT
                                            │                                          dashboard (SSE)
                                            ▼
      oracle ─► translate ─► verify ─► repair loop (escalating) ─► fuzz K rounds ─► VERIFIED ─► reflector
        │                      │                                     │
        └──── original program runs (sandbox) ──── translated code runs (sandbox) ────┘
```

Agents never call each other. Each stage reads its inputs from the blackboard and writes its
results back, and every state change appends a row to `events`. Three things follow from this:
the CLI progress view and the dashboard are plain readers, a killed run resumes from the
database (`migrate --resume`), and the scheduler is the only component that decides *when*
things run.

## Components

| Path | Role |
|---|---|
| `db.py` | Blackboard. Statuses: `pending → oracle → ready → translating → verifying → repairing → fuzzing → verified`, or `blocked` / `dep_blocked`. `reset()` clears a run in place. |
| `graph.py` | Module graph (CALL and COPY edges). Strongly connected components become migration units, so a CALL cycle is translated together. Waves come from a topological order. |
| `scheduler.py` | Async work queue. A unit is ready when all its dependencies are VERIFIED. Oracles are built eagerly, because they don't depend on translations. A BLOCKED unit marks its dependents DEP_BLOCKED. A crash in one unit is isolated. Supports resume. |
| `pipeline.py` | Per-unit stages and feature switches (`PipelineOptions`: repair / fuzz / lessons / gate). The eval baselines are built from these switches. |
| `sandbox.py` | The only way legacy or generated code runs: a subprocess with a timeout, rlimits (CPU, output file size, no core dumps, and address space except on macOS), a scrubbed environment and a capped output size. On macOS, `sandbox-exec` denies network access. Each event loop gets its own semaphore. |
| `llm/` | litellm client with per-model token buckets (RPM/TPM/RPD/TPD), plus quotas persisted from `llm_calls`. It walks a fallback chain with backoff and jitter, keeps an on-disk response cache, parses structured output with pydantic (with a repair retry), and budgets context to fit `min(context, TPM)`. `MockProvider` is used for offline runs. |
| `agents/analyzer.py` | Discovers and parses modules, builds the plan, and persists it. Can optionally summarise modules with the LLM. |
| `agents/oracle.py` | Golden tests. Inputs come from three sources: LLM-proposed (semantic coverage), property/boundary (from PIC clauses and from the numeric literals each field is compared with), and seeded random. Each input is run through the original program to record stdout and exit code. Includes a determinism probe. |
| `agents/translator.py`, `agents/repair.py` | Translation prompt: COBOL source, idiom guide, the interface contract, signatures of verified dependencies, sample tests and lessons. The repair loop feeds back a structured diff of the failing tests. It escalates to the `repair_escalated` tier after `escalate_after` failures and stops at `max_attempts`. |
| `agents/verifier.py` | Deterministic. Exact match on stdout and exit code. The only allowed normalisations are the ones switched on in `config/pipeline.yaml`. Produces a structured first-difference report, with per-field detail for subprograms. |
| `agents/fuzzer.py` | Starts once a unit passes its oracle tests. Each round combines LLM inputs aimed at the *current* translation (the code is in the prompt) with deterministic mutations. New inputs are recorded as golden tests. A counterexample starts a fresh repair loop against all tests. The unit needs K consecutive clean rounds. |
| `agents/reflector.py` | After a unit needed a repair, diffs the last failing attempt against the passing one and asks for 1–2 general lessons tagged with feature tags. Later prompts include the lessons whose tags overlap the unit's features most. |
| `agents/integrator.py` | Assembles a runnable package, replays the end-to-end scenarios on the original and on the package, and writes `REPORT.md` and `report.json`. |
| `dashboard/` | FastAPI. JSON endpoints plus an SSE stream that tails `events`. Cytoscape draws the unit graph. Read only. |
| `evaluation/runner.py` | `eval`: baselines A and B vs the full pipeline, scored on a held-out suite. See [EVAL.md](EVAL.md). |
| `adapters/base.py` | `LanguageAdapter`: everything language-specific lives behind it. |
| `adapters/cobol_python/` | COBOL parser, I/O layouts, cobc runner with a compile cache, COBOL driver generator, Python harness generator, input generators, runtime library `lbrt.py`, idiom guides, mock brain. |

## Making every module differential-testable

Main programs read a LINE SEQUENTIAL file on stdin and DISPLAY their results. Their exit code is
`RETURN-CODE`. They are compared directly.

Called subprograms have no I/O, so both sides get a generated driver that speaks the same text
protocol. Each stdin line is one CALL: the `USING` fields are `|`-separated and numerics use
`SIGN LEADING SEPARATE`. After each call, every parameter field is printed in the same format.

- The **COBOL driver** is generated from the parsed linkage section and compiled with the original
  subprogram. It CALLs the real code, so state carried between calls is part of the test.
- The **Python harness** parses the same fields into a dict, calls the translation, and prints the
  dict back. It is *strict*: a value that the COBOL field could not hold prints as `!!!…`. Without
  that, an overflow that COBOL truncates silently would compare equal by accident.

The translated code may import only the standard library and `lbrt`. `lbrt` is the COBOL runtime
semantics library: `store` handles truncation and ROUNDED into PIC sizes, `edit` handles edited
pictures (including a GnuCOBOL quirk), `alnum` pads and truncates, and there are date and
record-overlay helpers. Static checks reject anything else before the code is run.

## Verdicts and acceptance

```
for each attempt:  verify(all golden tests)  ── all identical? ──► fuzz rounds ── K clean? ──► VERIFIED
                          │ no                                         │ counterexample
                          ▼                                            ▼
                   structured diff → repair prompt (escalate)     add as golden test → repair loop
```

`VERIFIED` means the following, and nothing weaker:

- every recorded golden test is identical: oracle tests, plus every input added by fuzzing;
- K consecutive fuzz rounds found no divergence;
- the unit's dependencies were already VERIFIED when it was tested.

A unit that runs out of attempts is `BLOCKED`, with a reason (the last diff), and its dependents
become `DEP_BLOCKED`. `--resume --retry-blocked` retries them.

## Offline mode

`--dry-run` and every test use `MockProvider`. Its brain (`adapters/cobol_python/mock_brain.py`)
answers each role deterministically:

- **Oracle and fuzz inputs** are built from the input spec and from the literals in the code.
- **Translations** are the hand-written reference translations in `examples/bank_demo/reference_py/`.
  Each one verifies against GnuCOBOL. A fixed catalogue of realistic bugs is injected into them:
  string overwrite, truncation vs rounding, trailing spaces, an off-by-one page break, and a
  record overlay. Some bugs are only fixed by an escalated repair. One only shows up under
  fuzzing. A bug is skipped when a matching lesson is in the prompt.
- **Lessons** are produced when a bug's fix appears in the diff.

This makes the pipeline's own machinery testable end to end, with no network: the gate,
repair, escalation, fuzzing, lessons, resume and the integrator. Real runs never read
`reference_py/`.

## Adding a language pair

Implement `LanguageAdapter`. It covers:

- discovery and parsing, and the dependency edges;
- building and running the original;
- input spec, validation and generators (property, random, mutation, and optionally volume);
- the target contract and idiom guide;
- static checks;
- running a translation;
- packaging and scenarios;
- optionally, a mock brain.

The scheduler, LLM layer, verifier, fuzzer, reflector, integrator, dashboard and eval are
language-agnostic.
