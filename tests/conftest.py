import shutil
from pathlib import Path

import pytest

from legacybridge.config import PipelineConfig, ProvidersConfig, Settings, load_settings

REPO = Path(__file__).resolve().parents[1]
BANK_DEMO = REPO / "examples" / "bank_demo"

requires_cobc = pytest.mark.skipif(shutil.which("cobc") is None, reason="GnuCOBOL (cobc) not installed")


def make_providers(models: dict[str, dict], tiers: dict[str, list[str]] | None = None,
                   shared: dict[str, dict] | None = None) -> ProvidersConfig:
    """Build a ProvidersConfig from {'prov/alias': {limits...}}."""
    providers: dict = {}
    for ref, limits in models.items():
        pname, alias = ref.split("/")
        p = providers.setdefault(pname, {"api_key_env": f"{pname.upper()}_KEY", "models": {}})
        if shared and pname in shared:
            p["shared_limits"] = shared[pname]
        p["models"][alias] = {"litellm_model": f"{pname}/{alias}", **limits}
    tiers = tiers or {"strong": list(models), "fast": list(models)}
    return ProvidersConfig.model_validate({
        "defaults": {"retries_per_model": 2, "backoff_base_s": 1, "backoff_max_s": 4,
                     "backoff_jitter": 0.0, "max_wait_for_slot_s": 30},
        "providers": providers,
        "tiers": tiers,
        "roles": {r: "strong" for r in ["translator", "repair", "repair_escalated"]}
        | {r: "fast" for r in ["oracle", "fuzzer", "reflector", "summarizer"]},
        "mock": {"latency_ms": 0},
    })


@pytest.fixture
def settings_factory(tmp_path):
    def make(providers: ProvidersConfig | None = None, dry_run: bool = False, **pipeline) -> Settings:
        base = load_settings(REPO / "config", dry_run=dry_run)
        pipe = base.pipeline.model_copy(update={"state_dir": str(tmp_path / "state"),
                                                "cache_dir": str(tmp_path / "cache"), **pipeline})
        return Settings(providers=providers or base.providers, pipeline=pipe, dry_run=dry_run)
    return make
