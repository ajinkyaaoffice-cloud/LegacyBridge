import asyncio

import pytest
from pydantic import BaseModel

from legacybridge.db import Blackboard
from legacybridge.llm.cache import ResponseCache
from legacybridge.llm.client import LLMClient
from legacybridge.llm.prompt import Prompt, chunk_text
from legacybridge.llm.ratelimit import Limiter, RateLimiterRegistry, TokenBucket
from legacybridge.llm.structured import extract_code_blocks, extract_json
from legacybridge.llm.types import (FatalError, LLMUnavailable, PromptTooLarge, RateLimited, RawCompletion,
                                    SlotUnavailable, TransientError)

from conftest import make_providers


class Scripted:
    """Fake provider replaying a script of results/exceptions."""

    cacheable = True

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    async def complete(self, req, api_key):
        self.calls.append(req)
        item = self.script.pop(0) if self.script else "ok"
        if isinstance(item, Exception):
            raise item
        return RawCompletion(item, 10, 5)


class Answer(BaseModel):
    value: int


class FakeSleep:
    """Fake sleep that advances a fake clock (pass .now as the client's clock)."""

    def __init__(self):
        self.slept = []
        self.t = 0.0

    def now(self):
        return self.t

    async def __call__(self, s):
        self.slept.append(s)
        self.t += s


@pytest.fixture
def env_keys(monkeypatch):
    for p in ("A", "B", "C"):
        monkeypatch.setenv(f"{p}_KEY", "k")


def client(settings, providers, db=None, **kw):
    sleep = kw.pop("sleep", None) or FakeSleep()
    clock = sleep.now if isinstance(sleep, FakeSleep) else __import__("time").monotonic
    return LLMClient(settings, db, providers=providers, cache=ResponseCache(settings.pipeline.cache_dir, True),
                     sleep=sleep, clock=clock, **kw)


async def test_fallback_on_429_then_success(settings_factory, env_keys, tmp_path):
    cfg = make_providers({"a/m1": {"rpm": 100}, "b/m2": {"rpm": 100}})
    a = Scripted(RateLimited("429"), RateLimited("429"), RateLimited("429"))
    b = Scripted("hello")
    db = Blackboard(tmp_path / "s.db")
    c = client(settings_factory(cfg), {"a": a, "b": b}, db)
    res = await c.complete("translator", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert res.text == "hello" and res.provider == "b" and res.fallbacks == 1
    assert len(a.calls) == 3  # 1 + 2 retries
    statuses = [r["status"] for r in db.query("SELECT status FROM llm_calls ORDER BY id")]
    assert statuses == ["rate_limited"] * 3 + ["ok"]
    types = [e["type"] for e in db.events_after(0)]
    assert "fallback" in types and types.count("rate_limit_wait") <= 2


async def test_transient_retry_with_backoff(settings_factory, env_keys):
    cfg = make_providers({"a/m1": {}})
    a = Scripted(TransientError("503"), TransientError("timeout"), "fine")
    sleep = FakeSleep()
    c = client(settings_factory(cfg), {"a": a}, sleep=sleep)
    res = await c.complete("translator", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert res.text == "fine" and sleep.slept == [1.0, 2.0]  # exponential, jitter disabled


async def test_fatal_falls_through_immediately(settings_factory, env_keys):
    cfg = make_providers({"a/m1": {}, "b/m2": {}})
    a, b = Scripted(FatalError("401")), Scripted("x")
    c = client(settings_factory(cfg), {"a": a, "b": b})
    res = await c.complete("translator", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert len(a.calls) == 1 and res.provider == "b"


async def test_all_fail_raises(settings_factory, env_keys):
    cfg = make_providers({"a/m1": {}})
    c = client(settings_factory(cfg), {"a": Scripted(*[FatalError("x")] * 5)})
    with pytest.raises(LLMUnavailable):
        await c.complete("translator", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])


async def test_missing_keys_skip_provider(settings_factory, monkeypatch):
    monkeypatch.delenv("A_KEY", raising=False)
    cfg = make_providers({"a/m1": {}})
    c = LLMClient(settings_factory(cfg))  # no injected provider, no key
    with pytest.raises(LLMUnavailable, match="API keys"):
        await c.complete("translator", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])


async def test_structured_output_repair(settings_factory, env_keys):
    cfg = make_providers({"a/m1": {"json_mode": True}})
    a = Scripted("not json at all", '```json\n{"value": "NaN?"}\n```', 'Sure! {"value": 42}')
    c = client(settings_factory(cfg), {"a": a})
    res = await c.complete("oracle", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], Answer)
    assert res.parsed.value == 42
    assert len(a.calls) == 3
    assert a.calls[0].json_mode is True
    assert "JSON Schema" in a.calls[0].messages[0]["content"]
    assert a.calls[2].messages[-1]["role"] == "user" and "not valid" in a.calls[2].messages[-1]["content"]


async def test_cache_hit_second_time(settings_factory, env_keys, tmp_path):
    cfg = make_providers({"a/m1": {}})
    a = Scripted("first", "second")
    db = Blackboard(tmp_path / "s.db")
    c = client(settings_factory(cfg), {"a": a}, db)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "same"}]
    r1 = await c.complete("translator", msgs)
    r2 = await c.complete("translator", msgs)
    assert (r1.text, r1.cache_hit) == ("first", False)
    assert (r2.text, r2.cache_hit) == ("first", True)
    assert len(a.calls) == 1
    assert db.one("SELECT COUNT(*) n FROM llm_calls WHERE cache_hit=1")["n"] == 1


async def test_prompt_too_large_skips_small_model(settings_factory, env_keys):
    cfg = make_providers({"a/small": {"context_tokens": 600, "max_output_tokens": 100},
                          "b/big": {"context_tokens": 100000}})
    a, b = Scripted("x"), Scripted("y")
    c = client(settings_factory(cfg), {"a": a, "b": b})
    p = Prompt("sys").add("source", "X" * 10000, required=True)
    res = await c.complete("translator", p)
    assert res.provider == "b" and a.calls == []


async def test_tpm_too_small_skips_model(settings_factory, env_keys):
    cfg = make_providers({"a/tiny": {"tpm": 1000, "max_output_tokens": 500}, "b/big": {}})
    a, b = Scripted("x"), Scripted("y")
    c = client(settings_factory(cfg), {"a": a, "b": b})
    res = await c.complete("translator", Prompt("sys").add("src", "Y" * 4000, required=True))
    assert res.provider == "b"


async def test_dry_run_uses_mock(settings_factory):
    seen = []
    c = LLMClient(settings_factory(dry_run=True), mock_brain=lambda req: seen.append(req.meta) or '{"value": 7}')
    res = await c.complete("oracle", Prompt("s", instruction="go"), Answer, meta={"unit": "X"})
    assert res.parsed.value == 7 and res.provider == "mock" and seen == [{"unit": "X"}]


async def test_concurrent_requests_respect_rpm(settings_factory, env_keys):
    cfg = make_providers({"a/m1": {"rpm": 2}})
    cfg.defaults.max_wait_for_slot_s = 0.5  # 3rd request would wait 30s -> SlotUnavailable -> no models left
    c = client(settings_factory(cfg), {"a": Scripted("1", "2", "3")}, sleep=asyncio.sleep)
    msgs = lambda i: [{"role": "system", "content": "s"}, {"role": "user", "content": str(i)}]
    results = await asyncio.gather(*(c.complete("translator", msgs(i)) for i in range(3)), return_exceptions=True)
    assert sum(isinstance(r, LLMUnavailable) for r in results) == 1


# ---------------------------------------------------------------- unit pieces


def test_token_bucket_refill():
    t = [0.0]
    b = TokenBucket(60, 60, clock=lambda: t[0])
    b.take(60)
    assert b.wait_time(1) == pytest.approx(1.0)
    t[0] = 30
    assert b.wait_time(30) == 0
    assert b.wait_time(61) == float("inf")


async def test_limiter_daily_quota_and_shared_scope():
    fs = FakeSleep()
    reg = RateLimiterRegistry(sleep=fs, clock=fs.now)
    model = reg.get("p/m", rpd=2)
    shared = reg.get("p/*", rpm=100)
    await reg.acquire([model, shared], 10, 5)
    await reg.acquire([model, shared], 10, 5)
    with pytest.raises(SlotUnavailable):
        await reg.acquire([model, shared], 10, 5)
    assert shared.day_requests == 2


async def test_limiter_waits_then_proceeds():
    t = [0.0]
    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        t[0] += s

    reg = RateLimiterRegistry(sleep=sleep)
    lim = Limiter("x", rpm=1, clock=lambda: t[0])
    reg.limiters["x"] = lim
    await reg.acquire([lim], 1, 100)
    await reg.acquire([lim], 1, 100)
    assert sleeps and sleeps[0] == pytest.approx(60.01)


def test_prompt_budget_compacts_then_drops():
    p = (Prompt("sys", instruction="do it")
         .add("guide", "G" * 3500, priority=30, compact="short guide")
         .add("lessons", "L" * 3500, priority=20)
         .add("source", "S" * 700, priority=100, required=True))
    full = p.render(None)[1]["content"]
    assert "G" * 100 in full
    small = p.render(400)[1]["content"]
    assert "short guide" in small and "L" * 100 not in small and "S" * 700 in small
    with pytest.raises(PromptTooLarge):
        p.render(50)


def test_extract_json_and_code_blocks():
    assert extract_json('blah ```json\n{"a": [1, {"b": "}"}]}\n``` tail') == {"a": [1, {"b": "}"}]}
    assert extract_json('Here: {"x": 1} and more') == {"x": 1}
    text = "x\n```python\n# module: acctval\ndef a(): pass\n```\n```python\n# module: loanchk\ndef b(): pass\n```"
    blocks = extract_code_blocks(text)
    assert set(blocks) == {"acctval", "loanchk"}
    assert extract_code_blocks("```python\nprint(1)\n```") == {"": "print(1)\n"}


def test_chunk_text():
    chunks = chunk_text("\n".join(f"line {i}" for i in range(100)), max_tokens=20)
    assert len(chunks) > 3 and "".join(chunks).count("line") == 100
