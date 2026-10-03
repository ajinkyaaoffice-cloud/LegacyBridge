# LegacyBridge evaluation — dry-run (MockProvider)

- Corpus: `examples/bank_demo`; generated 2026-10-03 20:17:55
- Hidden suite: 660 tests recorded from the ORIGINAL programs (seed 20240917, up to 60 per module); inputs any configuration saw during migration are excluded from its score.

> **Dry run.** The MockProvider replays reference translations with a fixed catalogue of realistic bugs, so these numbers measure whether the pipeline machinery (gate, repair, escalation, fuzzing, lessons) catches and fixes them. They are not a measure of LLM quality. Run `legacybridge eval` with API keys for live numbers.

| metric | A: single-shot (no tests, no repair, ungated) | B: tests + repair loop (no fuzzing, no lessons) | Full: tests + repair + adversarial fuzzing + lessons |
|---|---|---|---|
| Modules correct on hidden suite | 6/11 | 10/11 | 11/11 |
| Hidden-test pass rate | 73.2% | 97.0% | 100.0% |
| Modules accepted (code shipped) | 11/11 | 11/11 | 11/11 |
| …of which wrong on hidden suite | 5 | 1 | 0 |
| Modules VERIFIED by the gate | 6/11 | 11/11 | 11/11 |
| …VERIFIED but wrong (false accept) | 0 | 1 | 0 |
| End-to-end scenarios identical | 2/4 | 3/4 | 4/4 |
| Translation/repair attempts | 10 | 15 | 16 |
| Fuzz counterexamples found | 0 | 0 | 1 |
| Lessons learned | 0 | 0 | 5 |
| LLM calls (cached) | 21 (0) | 26 (0) | 54 (0) |
| Tokens in / out | 78,527 / 29,376 | 112,641 / 35,084 | 213,686 / 99,796 |
| Wall time (s) | 9.4 | 13.7 | 34.0 |

## Per module (hidden tests passed / held-out total)

| module | baseline_a | baseline_b | full |
|---|---|---|---|
| ACCTVAL | ✓ 60/60 | ✓ 60/60 | ✓ 60/60 |
| AMORT | ✓ 60/60 | ✓ 60/60 | ✓ 60/60 |
| BALUPD | ✗ 0/60 (shipped unverified) | ✓ 60/60 | ✓ 60/60 |
| DATEUTIL | ✓ 60/60 | ✓ 60/60 | ✓ 60/60 |
| FEECALC | ✓ 60/60 | ✓ 60/60 | ✓ 60/60 |
| FMTUTIL | ✗ 40/60 (shipped unverified) | ✓ 60/60 | ✓ 60/60 |
| INTCALC | ✗ 48/60 (shipped unverified) | ✓ 60/60 | ✓ 60/60 |
| INTPOST | ✗ 35/60 (shipped unverified) | ✓ 60/60 | ✓ 60/60 |
| LOANCHK | ✓ 60/60 | ✓ 60/60 | ✓ 60/60 |
| LOANSCHD | ✓ 60/60 | ✓ 60/60 | ✓ 60/60 |
| STMTRPT | ✗ 0/60 (shipped unverified) | ✗ 40/60 | ✓ 60/60 |

## Residual bugs (first hidden-test divergence)

- **baseline_a / BALUPD**: stdout line 1: expected `'REJECT 00001 AC0017   E5 ACCOUNT CLOSED             '` got `'REJECT 00001 AC0017   E5 ACCOUNT CLOSED'`
- **baseline_a / FMTUTIL**: stdout line 1: expected `'D|+00000000400|20220718|009518|07/18/2022N         '` got `'D|+00000000400|20220718|009518|07/18/2022          '`
- **baseline_a / INTCALC**: stdout line 1: expected `'+00000859662|980464|20740603|20820515|M|+067007977|+02862|00'` got `'+00000859662|980464|20740603|20820515|M|+067007978|+02862|00'`
- **baseline_a / INTPOST**: stdout line 2: expected `'AC0002   DAYS=     3 INT=         9.86 RC=00'` got `'AC0002   DAYS=     3 INT=         9.87 RC=00'`
- **baseline_a / STMTRPT**: stdout line 2: expected `'STATEMENT DATE 06/22/20266.57CR    '` got `'STATEMENT DATE 06/22/2026          '`
- **baseline_b / STMTRPT**: stdout line 15: expected `'--- CONTINUED ---'` got `'06/21 THU CAROL WU                       -813.21      33,760.36'`
