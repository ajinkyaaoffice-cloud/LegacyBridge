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
