"""Deterministic MockProvider used by tests and `--dry-run`.

It holds no domain knowledge itself: a "brain" callable (supplied by the language adapter, see
adapters/cobol_python/mock_brain.py) maps a request to a reply. Same request -> same reply.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from .types import LLMRequest, RawCompletion
from .prompt import estimate_tokens

Brain = Callable[[LLMRequest], str]


def default_brain(req: LLMRequest) -> str:
    """Fallback brain: empty JSON for structured requests, a fixed string otherwise."""
    if req.json_mode or "JSON Schema" in req.messages[0]["content"]:
        return "{}"
    return "MOCK RESPONSE"


class MockProvider:
    """Offline provider. Never touches the network."""

    cacheable = False

    def __init__(self, brain: Brain | None = None, latency_ms: int = 0):
        self.brain = brain or default_brain
        self.latency_ms = latency_ms
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, api_key: str | None) -> RawCompletion:
        self.calls.append(req)
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        text = self.brain(req)
        prompt_text = "".join(m["content"] for m in req.messages)
        return RawCompletion(text=text, prompt_tokens=estimate_tokens(prompt_text),
                             completion_tokens=estimate_tokens(text))
