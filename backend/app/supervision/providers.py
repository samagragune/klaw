"""Model-provider-independent evaluator transport.

Each provider implements one method: turn a (system, prompt) pair into text.
Nothing above this layer knows which vendor is configured, and no provider is
hardcoded anywhere else in the codebase.

All adapters speak raw HTTP via httpx, so adding a provider costs one class
and no new dependency.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class EvaluatorUnavailable(RuntimeError):
    """Raised when LLM evaluation is requested but not configured."""


class LLMProvider(ABC):
    name: str = "unknown"

    def __init__(self, model: str, api_key: str, base_url: str = "") -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url

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
            response = await client.post(
                url, json=body, headers={"x-goog-api-key": self._api_key}
            )
            response.raise_for_status()
            data = response.json()
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
            response = await client.post(
                f"{base}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            data = response.json()
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
            response = await client.post(
                f"{base}/messages",
                json=body,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            response.raise_for_status()
            data = response.json()
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


def get_provider() -> LLMProvider:
    """Build the configured provider, or raise ``EvaluatorUnavailable``."""
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
    return cls(
        model=settings.supervisor_model,
        api_key=settings.supervisor_llm_api_key,
        base_url=settings.supervisor_llm_base_url,
    )


def provider_configured() -> bool:
    return get_settings().supervisor_llm_provider != "none"
