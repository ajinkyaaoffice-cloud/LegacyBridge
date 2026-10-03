"""LLMClient: one async `complete(role, prompt, schema)` over free providers.

Per call it walks the role's fallback chain (config/providers.yaml):
  budget check -> cache -> rate limiter (RPM/TPM/daily, per model + per provider)
  -> provider call with retries (exponential backoff + jitter on 429/5xx/timeout)
  -> optional pydantic validation with repair prompts -> cache + log to llm_calls.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections import Counter
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from ..config import ModelSpec, ProviderSpec, Settings
from ..db import Blackboard
from .cache import ResponseCache
from .mock import Brain, MockProvider
from .prompt import Prompt, estimate_tokens
from .providers import LiteLLMProvider, Provider
from .ratelimit import Limiter, RateLimiterRegistry, utc_day_start
from .structured import parse_structured, repair_message, schema_instruction
from .types import (FatalError, LLMRequest, LLMResult, LLMUnavailable, PromptTooLarge, RateLimited,
                    SlotUnavailable, TransientError)

MOCK_REF = "mock/mock"
JSON_REPAIR_ATTEMPTS = 2


class LLMClient:
    """Async LLM gateway shared by all agents."""

    def __init__(
        self,
        settings: Settings,
        db: Blackboard | None = None,
        *,
        providers: dict[str, Provider] | None = None,
        cache: ResponseCache | None = None,
        mock_brain: Brain | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        self.settings = settings
        self.cfg = settings.providers
        self.db = db
        self.sleep = sleep
        self.rng = rng or random.Random(0)
        self.registry = RateLimiterRegistry(sleep=sleep, clock=clock)
        pipe = settings.pipeline
        self.cache = cache or ResponseCache(pipe.cache_dir + "/llm", enabled=not settings.dry_run)
        self.providers: dict[str, Provider] = providers or {}
        self.providers.setdefault("mock", MockProvider(mock_brain, self.cfg.mock.latency_ms))
        self.stats: Counter[str] = Counter()
        self._seeded: set[str] = set()

    # ------------------------------------------------------------------ chain resolution

    def _provider(self, name: str) -> Provider:
        if name not in self.providers:
            self.providers[name] = LiteLLMProvider()
        return self.providers[name]

    def _api_key(self, spec: ProviderSpec) -> str | None:
        return os.environ.get(spec.api_key_env) or None

    def available_models(self, chain: list[str]) -> list[str]:
        """Filter a chain to enabled providers that have an API key (or an injected provider)."""
        out = []
        for ref in chain:
            pname, spec, _, _ = self.cfg.model(ref)
            if not spec.enabled:
                continue
            if self._api_key(spec) or pname in self.providers:
                out.append(ref)
        return out

    def chain(self, role: str, tier: str | None = None) -> list[str]:
        """Ordered model refs to try for `role` (or an explicit tier)."""
        if self.settings.dry_run:
            return [MOCK_REF]
        refs = list(self.cfg.tiers[tier]) if tier else self.cfg.chain_for_role(role)
        return self.available_models(refs)

    def _spec(self, ref: str) -> tuple[str, ProviderSpec | None, ModelSpec]:
        if ref == MOCK_REF:
            return "mock", None, ModelSpec(litellm_model="mock", context_tokens=200_000, max_output_tokens=16_000)
        pname, pspec, _, mspec = self.cfg.model(ref)
        return pname, pspec, mspec

    def _limiters(self, ref: str, pname: str, pspec: ProviderSpec | None, mspec: ModelSpec) -> list[Limiter]:
        scopes = [self.registry.get(ref, rpm=mspec.rpm, tpm=mspec.tpm, rpd=mspec.rpd, tpd=mspec.tpd)]
        if pspec and pspec.shared_limits:
            sl = pspec.shared_limits
            scopes.append(self.registry.get(f"{pname}/*", rpm=sl.rpm, tpm=sl.tpm, rpd=sl.rpd, tpd=sl.tpd))
        # seed daily usage from the blackboard once per scope (quota survives restarts)
        if self.db is not None:
            for s in scopes:
                if s.name not in self._seeded:
                    self._seeded.add(s.name)
                    model = None if s.name.endswith("/*") else ref
                    n, t = self.db.llm_usage_since(pname, model, utc_day_start())
                    s.day_requests, s.day_tokens = n, t
        return scopes

    # ------------------------------------------------------------------ logging

    def _log(self, *, role: str, unit: str | None, provider: str, model: str, status: str,
             prompt_tokens: int = 0, completion_tokens: int = 0, latency_ms: float = 0.0,
             cache_hit: bool = False, error: str | None = None) -> None:
        self.stats[f"status:{status}"] += 1
        if status == "ok":
            self.stats["calls"] += 1
            self.stats["tokens"] += prompt_tokens + completion_tokens
            self.stats[f"provider:{provider}"] += 1
            if cache_hit:
                self.stats["cache_hits"] += 1
        if self.db is None:
            return
        self.db.log_llm_call(role=role, unit=unit, provider=provider, model=model, status=status,
                             prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                             latency_ms=latency_ms, cache_hit=int(cache_hit), error=error)
        self.db.emit("llm_call", unit, role=role, provider=provider, model=model, status=status,
                     cache_hit=cache_hit, latency_ms=round(latency_ms, 1),
                     tokens=prompt_tokens + completion_tokens)

    def _event(self, type_: str, unit: str | None, **payload: Any) -> None:
        self.stats[type_] += 1
        if self.db is not None:
            self.db.emit(type_, unit, **payload)

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        d = self.cfg.defaults
        base = retry_after if retry_after else min(d.backoff_max_s, d.backoff_base_s * (2 ** attempt))
        return max(0.0, base * (1 + self.rng.uniform(-d.backoff_jitter, d.backoff_jitter)))

    # ------------------------------------------------------------------ main entry

    async def complete(
        self,
        role: str,
        prompt: Prompt | list[dict[str, str]],
        schema: type[BaseModel] | None = None,
        *,
        meta: dict[str, Any] | None = None,
        unit: str | None = None,
        tier: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """Complete a prompt for an agent role, with fallback across the configured chain."""
        chain = self.chain(role, tier)
        if not chain:
            raise LLMUnavailable(
                f"no usable model for role {role!r}: set API keys in .env (see .env.example) or use --dry-run"
            )
        d = self.cfg.defaults
        ctx = self.settings.pipeline.context
        errors: list[str] = []
        for idx, ref in enumerate(chain):
            pname, pspec, mspec = self._spec(ref)
            out_tokens = min(max_tokens or mspec.max_output_tokens, mspec.max_output_tokens)
            budget = mspec.context_tokens - out_tokens
            if mspec.tpm:
                budget = min(budget, mspec.tpm - out_tokens)
            suffix = schema_instruction(schema) if schema else ""
            try:
                if isinstance(prompt, Prompt):
                    messages = prompt.render(budget, ctx.chars_per_token, system_suffix=suffix)
                else:
                    messages = [dict(m) for m in prompt]
                    if suffix:
                        messages[0] = {**messages[0], "content": messages[0]["content"] + "\n\n" + suffix}
                    if estimate_tokens("".join(m["content"] for m in messages), ctx.chars_per_token) > budget:
                        raise PromptTooLarge("raw messages exceed budget")
            except PromptTooLarge as exc:
                self._log(role=role, unit=unit, provider=pname, model=ref, status="skipped", error=str(exc))
                errors.append(f"{ref}: {exc}")
                continue

            req = LLMRequest(
                role=role, model_ref=ref, litellm_model=mspec.litellm_model, messages=messages,
                temperature=d.temperature if temperature is None else temperature, max_tokens=out_tokens,
                json_mode=bool(schema and mspec.json_mode), timeout_s=d.timeout_s, meta=dict(meta or {}),
            )
            try:
                result = await self._complete_on_model(req, pname, pspec, mspec, schema, unit)
                result.fallbacks = idx
                return result
            except (RateLimited, TransientError, FatalError, SlotUnavailable, ValueError) as exc:
                errors.append(f"{ref}: {type(exc).__name__}: {exc}")
                if idx + 1 < len(chain):
                    self._event("fallback", unit, role=role, frm=ref, to=chain[idx + 1],
                                reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        raise LLMUnavailable(f"all models failed for role {role!r}: " + " | ".join(errors[-6:]))

    async def _complete_on_model(self, req: LLMRequest, pname: str, pspec: ProviderSpec | None,
                                 mspec: ModelSpec, schema: type[BaseModel] | None, unit: str | None) -> LLMResult:
        provider = self._provider(pname)
        api_key = self._api_key(pspec) if pspec else None
        params = {"temperature": req.temperature, "max_tokens": req.max_tokens, "json": req.json_mode}
        messages = req.messages
        for json_try in range(JSON_REPAIR_ATTEMPTS + 1):
            key = ResponseCache.key(req.litellm_model, messages, params)
            cached = self.cache.get(key) if provider.cacheable else None
            if cached is not None:
                parsed = None
                if schema is not None:
                    try:
                        parsed = parse_structured(cached["text"], schema)
                    except ValueError:
                        cached = None
                if cached is not None:
                    self._log(role=req.role, unit=unit, provider=pname, model=req.model_ref, status="ok",
                              prompt_tokens=cached.get("prompt_tokens", 0),
                              completion_tokens=cached.get("completion_tokens", 0), cache_hit=True)
                    return LLMResult(text=cached["text"], parsed=parsed, model_ref=req.model_ref, provider=pname,
                                     prompt_tokens=cached.get("prompt_tokens", 0),
                                     completion_tokens=cached.get("completion_tokens", 0), cache_hit=True)

            call_req = LLMRequest(**{**req.__dict__, "messages": messages})
            raw, latency = await self._call_with_retries(call_req, provider, api_key, pname, pspec, mspec, unit)
            if schema is None:
                if provider.cacheable:
                    self.cache.put(key, raw.__dict__)
                return LLMResult(text=raw.text, model_ref=req.model_ref, provider=pname,
                                 prompt_tokens=raw.prompt_tokens, completion_tokens=raw.completion_tokens,
                                 latency_ms=latency)
            try:
                parsed = parse_structured(raw.text, schema)
            except ValueError as exc:
                self._event("invalid_json", unit, model=req.model_ref, role=req.role, error=str(exc)[:200])
                if json_try == JSON_REPAIR_ATTEMPTS:
                    raise ValueError(f"invalid structured output after {JSON_REPAIR_ATTEMPTS} repairs: {exc}")
                messages = [*messages, {"role": "assistant", "content": raw.text[:4000]}, repair_message(str(exc))]
                continue
            if provider.cacheable:
                self.cache.put(key, raw.__dict__)
            return LLMResult(text=raw.text, parsed=parsed, model_ref=req.model_ref, provider=pname,
                             prompt_tokens=raw.prompt_tokens, completion_tokens=raw.completion_tokens,
                             latency_ms=latency)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _call_with_retries(self, req: LLMRequest, provider: Provider, api_key: str | None, pname: str,
                                 pspec: ProviderSpec | None, mspec: ModelSpec, unit: str | None):
        d = self.cfg.defaults
        scopes = self._limiters(req.model_ref, pname, pspec, mspec)
        est = estimate_tokens("".join(m["content"] for m in req.messages),
                              self.settings.pipeline.context.chars_per_token) + req.max_tokens // 4
        last_exc: Exception | None = None
        for attempt in range(d.retries_per_model + 1):
            def on_wait(w: float) -> None:
                self._event("rate_limit_wait", unit, model=req.model_ref, wait_s=round(w, 2), role=req.role)

            try:
                await self.registry.acquire(scopes, est, d.max_wait_for_slot_s, on_wait)
            except SlotUnavailable as exc:
                self._log(role=req.role, unit=unit, provider=pname, model=req.model_ref, status="skipped",
                          error=str(exc))
                raise
            start = time.perf_counter()
            try:
                raw = await provider.complete(req, api_key)
            except RateLimited as exc:
                last_exc = exc
                wait = self._backoff(attempt, exc.retry_after)
                for s in scopes:
                    s.cooldown(wait)
                self._log(role=req.role, unit=unit, provider=pname, model=req.model_ref, status="rate_limited",
                          latency_ms=(time.perf_counter() - start) * 1000, error=str(exc)[:300])
                if wait > d.max_wait_for_slot_s or attempt == d.retries_per_model:
                    raise
                # the cooldown makes the next acquire() wait; no separate sleep needed
                self._event("backoff", unit, model=req.model_ref, wait_s=round(wait, 2), reason="429")
                continue
            except TransientError as exc:
                last_exc = exc
                status = "timeout" if "timeout" in str(exc).lower() else "error"
                self._log(role=req.role, unit=unit, provider=pname, model=req.model_ref, status=status,
                          latency_ms=(time.perf_counter() - start) * 1000, error=str(exc)[:300])
                if attempt == d.retries_per_model:
                    raise
                wait = self._backoff(attempt)
                self._event("backoff", unit, model=req.model_ref, wait_s=round(wait, 2), reason=status)
                await self.sleep(wait)
                continue
            except FatalError as exc:
                self._log(role=req.role, unit=unit, provider=pname, model=req.model_ref, status="error",
                          latency_ms=(time.perf_counter() - start) * 1000, error=str(exc)[:300])
                raise
            latency = (time.perf_counter() - start) * 1000
            actual = raw.prompt_tokens + raw.completion_tokens
            if actual:
                for s in scopes:
                    s.settle(est, actual)
            self._log(role=req.role, unit=unit, provider=pname, model=req.model_ref, status="ok",
                      prompt_tokens=raw.prompt_tokens, completion_tokens=raw.completion_tokens, latency_ms=latency)
            return raw, latency
        raise last_exc or TransientError("retries exhausted")  # pragma: no cover
