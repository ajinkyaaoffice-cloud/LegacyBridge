"""Provider adapters. LiteLLMProvider talks to real free APIs; MockProvider lives in mock.py."""

from __future__ import annotations

from typing import Protocol

from .types import FatalError, LLMRequest, RateLimited, RawCompletion, TransientError


class Provider(Protocol):
    """Anything that can complete an LLMRequest."""

    cacheable: bool

    async def complete(self, req: LLMRequest, api_key: str | None) -> RawCompletion: ...


class LiteLLMProvider:
    """Real hosted models via litellm, with litellm exceptions mapped to our error classes."""

    cacheable = True

    async def complete(self, req: LLMRequest, api_key: str | None) -> RawCompletion:
        import litellm

        litellm.suppress_debug_info = True
        kwargs: dict = {
            "model": req.litellm_model,
            "messages": req.messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "timeout": req.timeout_s,
            "num_retries": 0,  # retries/fallback are ours
        }
        if api_key:
            kwargs["api_key"] = api_key
        if req.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await litellm.acompletion(**kwargs)
        except litellm.RateLimitError as exc:
            raise RateLimited(str(exc)[:300], _retry_after(exc)) from exc
        except (litellm.Timeout, litellm.APIConnectionError, litellm.ServiceUnavailableError,
                litellm.InternalServerError) as exc:
            raise TransientError(str(exc)[:300]) from exc
        except (litellm.AuthenticationError, litellm.NotFoundError, litellm.BadRequestError,
                litellm.ContextWindowExceededError, litellm.PermissionDeniedError) as exc:
            raise FatalError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
        except litellm.APIError as exc:
            status = getattr(exc, "status_code", 500) or 500
            if status == 429:
                raise RateLimited(str(exc)[:300]) from exc
            if status >= 500:
                raise TransientError(str(exc)[:300]) from exc
            raise FatalError(str(exc)[:300]) from exc
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = getattr(resp, "usage", None)
        return RawCompletion(
            text=text,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def _retry_after(exc: Exception) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
