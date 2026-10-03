"""Configuration loading: config/providers.yaml and config/pipeline.yaml -> pydantic models."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- providers.yaml


class ModelSpec(BaseModel):
    litellm_model: str
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None
    context_tokens: int = 32768
    max_output_tokens: int = 4096
    json_mode: bool = False


class Limits(BaseModel):
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None


class ProviderSpec(BaseModel):
    api_key_env: str
    enabled: bool = True
    shared_limits: Limits | None = None
    models: dict[str, ModelSpec]


class ProviderDefaults(BaseModel):
    timeout_s: float = 120
    temperature: float = 0.2
    retries_per_model: int = 2
    backoff_base_s: float = 2.0
    backoff_max_s: float = 45.0
    backoff_jitter: float = 0.3
    max_wait_for_slot_s: float = 90


class MockSpec(BaseModel):
    latency_ms: int = 20


class ProvidersConfig(BaseModel):
    defaults: ProviderDefaults = ProviderDefaults()
    providers: dict[str, ProviderSpec]
    tiers: dict[str, list[str]]
    roles: dict[str, str]
    mock: MockSpec = MockSpec()

    def chain_for_role(self, role: str) -> list[str]:
        """Return the ordered model refs ('provider/alias') for an agent role."""
        tier = self.roles.get(role)
        if tier is None:
            raise KeyError(f"unknown LLM role {role!r}; add it to config/providers.yaml roles")
        return list(self.tiers[tier])

    def model(self, ref: str) -> tuple[str, ProviderSpec, str, ModelSpec]:
        """Resolve 'provider/alias' to (provider_name, provider_spec, alias, model_spec)."""
        provider, _, alias = ref.partition("/")
        spec = self.providers[provider]
        return provider, spec, alias, spec.models[alias]


# ---------------------------------------------------------------- pipeline.yaml


class SchedulerCfg(BaseModel):
    concurrency: int = 4
    oracle_concurrency: int = 4


class RepairCfg(BaseModel):
    max_attempts: int = 4
    escalate_after: int = 2


class FuzzerCfg(BaseModel):
    enabled: bool = True
    rounds: int = 2
    llm_inputs_per_round: int = 8
    mutation_inputs_per_round: int = 12


class LessonsCfg(BaseModel):
    enabled: bool = True
    max_in_prompt: int = 5


class OracleCfg(BaseModel):
    llm_cases: int = 10
    property_cases: int = 12
    random_cases: int = 12
    seed: int = 1337


class NormalizeCfg(BaseModel):
    rstrip_lines: bool = False
    ignore_trailing_blank_lines: bool = False


class VerifierCfg(BaseModel):
    normalize: NormalizeCfg = NormalizeCfg()


class SandboxCfg(BaseModel):
    timeout_s: float = 10
    cpu_seconds: int = 10
    memory_mb: int = 512
    max_output_bytes: int = 1_000_000
    parallelism: int = 8
    macos_seatbelt: bool = True


class ContextCfg(BaseModel):
    reserve_output_tokens: int = 3000
    chars_per_token: float = 3.5


class PipelineConfig(BaseModel):
    state_dir: str = ".legacybridge"
    cache_dir: str = ".lbcache"
    scheduler: SchedulerCfg = SchedulerCfg()
    repair: RepairCfg = RepairCfg()
    fuzzer: FuzzerCfg = FuzzerCfg()
    lessons: LessonsCfg = LessonsCfg()
    oracle: OracleCfg = OracleCfg()
    verifier: VerifierCfg = VerifierCfg()
    sandbox: SandboxCfg = SandboxCfg()
    context: ContextCfg = ContextCfg()


class Settings(BaseModel):
    """Everything a run needs: both config files plus runtime switches."""

    providers: ProvidersConfig
    pipeline: PipelineConfig
    dry_run: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


def config_dir() -> Path:
    """Directory holding providers.yaml / pipeline.yaml (env LEGACYBRIDGE_CONFIG_DIR overrides)."""
    env = os.environ.get("LEGACYBRIDGE_CONFIG_DIR")
    if env:
        return Path(env)
    local = Path.cwd() / "config"
    if (local / "providers.yaml").exists():
        return local
    return REPO_ROOT / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def load_settings(cfg_dir: Path | None = None, *, dry_run: bool = False, **overrides: Any) -> Settings:
    """Load settings from YAML. `overrides` are dotted pipeline keys, e.g. scheduler__concurrency=2."""
    cfg_dir = cfg_dir or config_dir()
    try:
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env", override=False)
    except ImportError:  # pragma: no cover
        pass
    providers = ProvidersConfig.model_validate(_load_yaml(cfg_dir / "providers.yaml"))
    raw_pipeline = _load_yaml(cfg_dir / "pipeline.yaml")
    for key, value in overrides.items():
        if value is None:
            continue
        node = raw_pipeline
        parts = key.split("__")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    pipeline = PipelineConfig.model_validate(raw_pipeline)
    return Settings(providers=providers, pipeline=pipeline, dry_run=dry_run)
