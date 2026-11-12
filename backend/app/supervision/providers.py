"""Model-provider-independent evaluator transport.

Each provider implements one method: turn a (system, prompt) pair into text.
Nothing above this layer knows which vendor is configured, and no provider is
hardcoded anywhere else in the codebase.

All adapters speak raw HTTP via httpx, so adding a provider costs one class
and no new dependency.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class EvaluatorUnavailable(RuntimeError):
    """Raised when LLM evaluation is requested but not configured."""


RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


async def _post(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """POST with exponential backoff on rate limits and transient overload.

    Hosted models regularly shed load with 429/503; a supervisor that gives up
    on the first one is less reliable than the agents it watches.
    """
    delay = 1.5
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.post(url, **kwargs)
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                return response
            logger.warning(
                "evaluator: %s returned %s, retrying (%d/%d)",
                response.request.url.host, response.status_code, attempt, MAX_ATTEMPTS,
            )
        await asyncio.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


def _raise_for_status(response: httpx.Response) -> None:
    """Like ``raise_for_status`` but keeps the provider's error body.

    Providers explain retired models, quota and auth problems in the body;
    a bare status code throws that away.
    """
    if response.is_success:
        return
    detail = response.text[:500].strip()
    raise httpx.HTTPStatusError(
        f"{response.status_code} from {response.request.url.host}: {detail}",
        request=response.request,
        response=response,
    )


class LLMProvider(ABC):
    name: str = "unknown"

    def __init__(self, model: str, api_key: str, base_url: str = "") -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        #: (prompt_tokens, completion_tokens) of the most recent call, as the
        #: provider reported them. Used for the evaluation-cost view.
        self.last_usage: tuple[int, int] = (0, 0)

    @abstractmethod
    async def complete(self, system: str, prompt: str) -> str: ...


class GeminiProvider(LLMProvider):
    name = "gemini"
    DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"

    async def complete(self, system: str, prompt: str) -> str:
        base = self._base_url or self.DEFAULT_BASE
        url = f"{base}/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await _post(
                client, url, json=body, headers={"x-goog-api-key": self._api_key}
            )
            _raise_for_status(response)
            data = response.json()
        usage = data.get("usageMetadata") or {}
        self.last_usage = (
            int(usage.get("promptTokenCount") or 0),
            int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
        )
        candidates = data.get("candidates") or []
        if not candidates:
            raise EvaluatorUnavailable("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts)


class OpenAIProvider(LLMProvider):
    name = "openai"
    DEFAULT_BASE = "https://api.openai.com/v1"

    async def complete(self, system: str, prompt: str) -> str:
        base = self._base_url or self.DEFAULT_BASE
        body = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await _post(
                client,
                f"{base}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            _raise_for_status(response)
            data = response.json()
        usage = data.get("usage") or {}
        self.last_usage = (
            int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        )
        return data["choices"][0]["message"]["content"]


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    DEFAULT_BASE = "https://api.anthropic.com/v1"

    async def complete(self, system: str, prompt: str) -> str:
        base = self._base_url or self.DEFAULT_BASE
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0.1,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await _post(
                client,
                f"{base}/messages",
                json=body,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            _raise_for_status(response)
            data = response.json()
        usage = data.get("usage") or {}
        self.last_usage = (
            int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        )
        return "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )


class FakeProvider(LLMProvider):
    """Deterministic provider for tests and offline demos.

    It echoes the evidence it was given back as a structured response, which
    is exactly what a well-behaved evaluator should do: never assert anything
    the trace does not contain.
    """

    name = "fake"

    def __init__(self, model: str = "fake-model", api_key: str = "", base_url: str = "") -> None:
        super().__init__(model, api_key, base_url)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return (
            '{"summary": "Offline evaluator: no model configured.", '
            '"likely_cause": "Not assessed - the deterministic detectors supplied the evidence '
            'for this incident.", "recommended_action": "Configure SUPERVISOR_LLM_PROVIDER to '
            'enable model-based diagnosis.", "confidence": "none"}'
        )


_PROVIDERS: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "fake": FakeProvider,
}


def get_provider(tier: str = "deep") -> LLMProvider:
    """Build the configured provider, or raise ``EvaluatorUnavailable``.

    ``tier="fast"`` uses ``SUPERVISOR_FAST_MODEL`` (routine screening);
    ``"deep"`` uses ``SUPERVISOR_MODEL`` (diagnosis, confirming a failure).
    With no fast model configured both tiers use the same model.
    """
    settings = get_settings()
    choice = settings.supervisor_llm_provider
    if choice == "none":
        raise EvaluatorUnavailable(
            "No supervisor LLM provider configured. Deterministic detection is "
            "unaffected; set SUPERVISOR_LLM_PROVIDER to enable model-based evaluation."
        )
    cls = _PROVIDERS.get(choice)
    if cls is None:  # pragma: no cover - guarded by Settings validation
        raise EvaluatorUnavailable(f"Unsupported provider: {choice}")
    if choice != "fake" and not settings.supervisor_llm_api_key:
        raise EvaluatorUnavailable(
            f"SUPERVISOR_LLM_PROVIDER={choice} but SUPERVISOR_LLM_API_KEY is empty."
        )
    model = (
        settings.supervisor_fast_model
        if tier == "fast" and settings.supervisor_fast_model
        else settings.supervisor_model
    )
    return cls(
        model=model,
        api_key=settings.supervisor_llm_api_key,
        base_url=settings.supervisor_llm_base_url,
    )


def provider_configured() -> bool:
    return get_settings().supervisor_llm_provider != "none"
