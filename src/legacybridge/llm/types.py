"""Shared types and errors for the LLM layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawCompletion:
    """What a provider returns for one call."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class LLMRequest:
    """A fully rendered request for one specific model."""

    role: str
    model_ref: str                 # 'provider/alias'
    litellm_model: str
    messages: list[dict[str, str]]
    temperature: float
    max_tokens: int
    json_mode: bool
    timeout_s: float
    meta: dict[str, Any] = field(default_factory=dict)  # task context (used by MockProvider only)


@dataclass
class LLMResult:
    """Result handed back to agents."""

    text: str
    parsed: Any = None             # pydantic model instance when a schema was given
    model_ref: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cache_hit: bool = False
    fallbacks: int = 0             # how many models were skipped/failed before this one


class LLMError(Exception):
    """Base class for provider errors."""


class RateLimited(LLMError):
    """HTTP 429 or provider-side quota error."""

    def __init__(self, msg: str = "rate limited", retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class TransientError(LLMError):
    """5xx, connection reset, timeout: worth retrying with backoff."""


class FatalError(LLMError):
    """Auth / bad request / unknown model / context exceeded: retrying the same model won't help."""


class SlotUnavailable(LLMError):
    """Local rate limiter can't serve this request soon enough (or ever) on this model."""


class PromptTooLarge(LLMError):
    """The prompt can't be reduced to fit this model's budget."""


class LLMUnavailable(LLMError):
    """Every model in the fallback chain failed."""
