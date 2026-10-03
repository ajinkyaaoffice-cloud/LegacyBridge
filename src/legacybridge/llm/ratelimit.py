"""Per-model and per-provider async rate limiting: token buckets for RPM/TPM, daily quotas.

Daily usage is seeded from the llm_calls table so quotas survive restarts (`--resume`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .types import SlotUnavailable


class TokenBucket:
    """Classic token bucket. `capacity` tokens, refilled continuously over `period_s`."""

    def __init__(self, capacity: float, period_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.capacity = float(capacity)
        self.rate = self.capacity / period_s
        self.tokens = self.capacity
        self.clock = clock
        self.updated = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def wait_time(self, amount: float) -> float:
        """Seconds until `amount` tokens are available (inf if it exceeds capacity)."""
        if amount > self.capacity:
            return float("inf")
        self._refill()
        if self.tokens >= amount:
            return 0.0
        return (amount - self.tokens) / self.rate

    def take(self, amount: float) -> None:
        self._refill()
        self.tokens -= amount

    def adjust(self, delta: float) -> None:
        """Debit (positive) or credit (negative) after learning the real token count."""
        self._refill()
        self.tokens = min(self.capacity, self.tokens - delta)


def utc_day_start(ts: float | None = None) -> float:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


@dataclass
class Limiter:
    """Limits for one scope (a model, or a provider's shared quota)."""

    name: str
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None
    clock: Callable[[], float] = time.monotonic
    day_requests: int = 0
    day_tokens: int = 0
    day_start: float = field(default_factory=utc_day_start)
    cooldown_until: float = 0.0  # set after a provider 429

    def __post_init__(self) -> None:
        self.rpm_bucket = TokenBucket(self.rpm, clock=self.clock) if self.rpm else None
        self.tpm_bucket = TokenBucket(self.tpm, clock=self.clock) if self.tpm else None

    def _roll_day(self) -> None:
        start = utc_day_start()
        if start > self.day_start:
            self.day_start, self.day_requests, self.day_tokens = start, 0, 0

    def wait_time(self, tokens: int) -> float:
        """Seconds until a request of `tokens` can be sent (inf = never today / too big)."""
        self._roll_day()
        if self.rpd is not None and self.day_requests >= self.rpd:
            return float("inf")
        if self.tpd is not None and self.day_tokens + tokens > self.tpd:
            return float("inf")
        waits = [max(0.0, self.cooldown_until - self.clock())]
        if self.rpm_bucket:
            waits.append(self.rpm_bucket.wait_time(1))
        if self.tpm_bucket:
            waits.append(self.tpm_bucket.wait_time(tokens))
        return max(waits)

    def take(self, tokens: int) -> None:
        self.day_requests += 1
        self.day_tokens += tokens
        if self.rpm_bucket:
            self.rpm_bucket.take(1)
        if self.tpm_bucket:
            self.tpm_bucket.take(tokens)

    def settle(self, estimated: int, actual: int) -> None:
        """Correct token accounting once the provider reports real usage."""
        delta = actual - estimated
        self.day_tokens += delta
        if self.tpm_bucket:
            self.tpm_bucket.adjust(delta)

    def cooldown(self, seconds: float) -> None:
        self.cooldown_until = max(self.cooldown_until, self.clock() + seconds)


class RateLimiterRegistry:
    """Holds limiters and acquires slots across a model scope and its provider scope atomically."""

    def __init__(self, sleep: Callable[[float], "asyncio.Future[None]"] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.limiters: dict[str, Limiter] = {}
        self._lock = asyncio.Lock()
        self._sleep = sleep or asyncio.sleep
        self.clock = clock

    def get(self, name: str, **limits: int | None) -> Limiter:
        if name not in self.limiters:
            self.limiters[name] = Limiter(name=name, clock=self.clock, **limits)
        return self.limiters[name]

    async def acquire(self, scopes: list[Limiter], tokens: int, max_wait_s: float,
                      on_wait: Callable[[float], None] | None = None) -> None:
        """Block until every scope can serve `tokens`; raise SlotUnavailable if the wait is too long.

        `on_wait` is called once, the first time this request has to wait."""
        notified = False
        while True:
            async with self._lock:
                wait = max((s.wait_time(tokens) for s in scopes), default=0.0)
                if wait == 0.0:
                    for s in scopes:
                        s.take(tokens)
                    return
            if wait > max_wait_s:
                raise SlotUnavailable(f"wait {wait:.1f}s exceeds {max_wait_s:.0f}s on "
                                      f"{', '.join(s.name for s in scopes)}")
            if on_wait and not notified:
                on_wait(wait)
                notified = True
            await self._sleep(min(wait, max_wait_s) + 0.01)
