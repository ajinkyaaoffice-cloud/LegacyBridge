# LegacyBridge — implementation plan

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Scaffolding: pyproject, config loading, SQLite blackboard, sandbox, MockProvider skeleton | db + sandbox unit tests pass |
| 1 | LLM layer: litellm provider, token-bucket RPM/TPM + daily quota, fallback chain w/ backoff+jitter, disk cache, pydantic structured output w/ repair retry, context budgeting, llm_calls logging | tests with fake providers (429/5xx/timeouts, invalid JSON) pass |
| 2 | `examples/bank_demo` (~15 COBOL files, cycle, COMP-3, ROUNDED, truncation, edited pics, dates); COBOL parser; dependency graph with SCC collapse; `analyze` | analyzer tests pass; all programs compile with cobc |
| 3 | Oracle builder: driver generation for CALLed subprograms, cobc compile cache, LLM test inputs + property/random inputs, golden outputs | golden outputs recorded for every program |
| 4 | Runtime lib `lbrt`, idiom guide, translator, harness generator, verifier (structured diff), repair loop with escalation; reference translations + mock translator | one module migrates end to end offline |
| 5 | Async dynamic scheduler over SCC DAG, concurrency limit, BLOCKED propagation, resume | full offline migrate of bank_demo, resume test |
| 6 | Adversarial fuzzer (LLM + deterministic literal-boundary mutations), K clean rounds; reflector + lessons retrieval | fuzzer finds a seeded hidden bug; lessons injected |
| 7 | Integrator: output package, CLI entrypoint, end-to-end scenario diff, report (md+json) | `report` works, e2e scenarios match |
| 8 | Dashboard: FastAPI + SSE + Cytoscape graph, module drawer, live panels | page renders live during a run |
| 9 | `eval` (Baseline A / B / Full), docs, README | results JSON+MD in eval/results |

Key design choices (details in ARCHITECTURE.md / ASSUMPTIONS.md):
- Programs talk via stdin/stdout. CALLed subprograms get an auto-generated COBOL driver
  (one CALL per input line, fields `|`-separated, numerics SIGN LEADING SEPARATE) and an
  auto-generated Python harness with the same format, so every module is differential-testable.
- Migration unit = strongly connected component of the CALL graph. Copybooks are data-only nodes.
- Offline mode: MockProvider serves reference translations with deterministic, catalogued bug
  injections, so the repair loop, fuzzer and lessons can be exercised and measured without network.
