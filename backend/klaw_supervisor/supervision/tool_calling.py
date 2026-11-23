"""Function calling across model providers: the Klaw agent's brain is swappable.

The agent loop speaks one neutral format; this module translates it for each
provider's API and back:

* ``gemini``     Google Gemini (``functionDeclarations``)
* ``openai``     OpenAI and every OpenAI-compatible endpoint - OpenRouter,
                 Groq, DeepSeek, Together, Mistral, vLLM, LM Studio - via
                 ``SUPERVISOR_LLM_BASE_URL``
* ``ollama``     a model running on this machine (OpenAI-compatible API at
                 http://localhost:11434/v1); no key, nothing leaves the machine
* ``anthropic``  Claude models (``tool_use`` blocks)

Neutral messages::

    {"role": "user", "text": "..."}
    {"role": "assistant", "text": "...", "calls": [{"id", "name", "args"}], "raw": <provider reply>}
    {"role": "tool", "results": [{"id", "name", "output"}]}

``converse`` returns ``{"text", "calls", "raw"}``.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from klaw_supervisor.supervision.providers import (
    REQUEST_TIMEOUT,
    EvaluatorUnavailable,
    LLMProvider,
    _post,
    _raise_for_status,
)

TOOL_PROVIDERS = {"gemini", "openai", "ollama", "anthropic"}


def supports_tools(provider: LLMProvider) -> bool:
    return bool(getattr(provider, "supports_tools", False)) or provider.name in TOOL_PROVIDERS


async def converse(
    provider: LLMProvider,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    force: str | None = None,
) -> dict[str, Any]:
    custom = getattr(provider, "converse", None)
    if custom is not None:  # tests and custom providers speak the neutral format directly
        return await custom(system, messages, tools, force=force)
    if provider.name == "gemini":
        return await _gemini(provider, system, messages, tools, force)
    if provider.name in ("openai", "ollama"):
        return await _openai(provider, system, messages, tools, force)
    if provider.name == "anthropic":
        return await _anthropic(provider, system, messages, tools, force)
    raise EvaluatorUnavailable(f"{provider.name} has no function calling here")


def _usage(provider: LLMProvider, prompt: Any, completion: Any) -> None:
    provider.last_usage = (int(prompt or 0), int(completion or 0))


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

async def _gemini(provider, system, messages, tools, force):
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["text"]}]})
        elif m["role"] == "assistant":
            # Sent back unchanged: Gemini needs its thought signatures.
            contents.append(m.get("raw") or {"role": "model", "parts": [
                {"functionCall": {"name": c["name"], "args": c["args"]}} for c in m.get("calls", [])
            ]})
        else:
            contents.append({"role": "user", "parts": [
                {"functionResponse": {"name": r["name"], "response": {"untrusted_output": r["output"]}}}
                for r in m["results"]
            ]})
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "tools": [{"functionDeclarations": tools}],
        "generationConfig": {"temperature": 0.2},
    }
    if force:
        body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [force]}}
    base = provider._base_url or "https://generativelanguage.googleapis.com/v1beta"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await _post(client, f"{base}/models/{provider.model}:generateContent", json=body,
                               headers={"x-goog-api-key": provider._api_key})
        _raise_for_status(response)
        data = response.json()
    usage = data.get("usageMetadata") or {}
    _usage(provider, usage.get("promptTokenCount"),
           int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0))
    candidates = data.get("candidates") or []
    if not candidates:
        raise EvaluatorUnavailable("Gemini returned no candidates")
    content = candidates[0].get("content") or {"role": "model", "parts": []}
    content.setdefault("role", "model")
    parts = content.setdefault("parts", [])
    calls = [
        {"id": uuid.uuid4().hex[:12], "name": p["functionCall"]["name"], "args": p["functionCall"].get("args") or {}}
        for p in parts if "functionCall" in p
    ]
    text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
    return {"text": text, "calls": calls, "raw": content}


# ---------------------------------------------------------------------------
# OpenAI and compatible (OpenRouter, Groq, DeepSeek, Ollama, LM Studio, vLLM)
# ---------------------------------------------------------------------------

async def _openai(provider, system, messages, tools, force):
    chat: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            chat.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("text") or None}
            if m.get("calls"):
                entry["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    for c in m["calls"]
                ]
            chat.append(entry)
        else:
            for r in m["results"]:
                chat.append({"role": "tool", "tool_call_id": r["id"],
                             "content": json.dumps({"untrusted_output": r["output"]})})
    body: dict[str, Any] = {
        "model": provider.model,
        "messages": chat,
        "tools": [{"type": "function", "function": t} for t in tools],
        "temperature": 0.2,
    }
    if force:
        body["tool_choice"] = {"type": "function", "function": {"name": force}}
    base = provider._base_url or getattr(provider, "DEFAULT_BASE", "https://api.openai.com/v1")
    headers = {"Authorization": f"Bearer {provider._api_key}"} if provider._api_key else {}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await _post(client, f"{base.rstrip('/')}/chat/completions", json=body, headers=headers)
        _raise_for_status(response)
        data = response.json()
    usage = data.get("usage") or {}
    _usage(provider, usage.get("prompt_tokens"), usage.get("completion_tokens"))
    message = (data.get("choices") or [{}])[0].get("message") or {}
    calls = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append({"id": call.get("id") or uuid.uuid4().hex[:12], "name": fn.get("name"), "args": args})
    return {"text": message.get("content") or "", "calls": calls, "raw": message}


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

async def _anthropic(provider, system, messages, tools, force):
    convo: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "user":
            convo.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            blocks = (m.get("raw") or {}).get("content")
            if blocks is None:
                blocks = ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                    for c in m.get("calls", [])
                ]
            convo.append({"role": "assistant", "content": blocks})
        else:
            convo.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"],
                 "content": json.dumps({"untrusted_output": r["output"]})}
                for r in m["results"]
            ]})
    body: dict[str, Any] = {
        "model": provider.model,
        "max_tokens": 2048,
        "system": system,
        "messages": convo,
        "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                  for t in tools],
    }
    if force:
        body["tool_choice"] = {"type": "tool", "name": force}
    base = provider._base_url or "https://api.anthropic.com/v1"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await _post(client, f"{base}/messages", json=body, headers={
            "x-api-key": provider._api_key, "anthropic-version": "2023-06-01"})
        _raise_for_status(response)
        data = response.json()
    usage = data.get("usage") or {}
    _usage(provider, usage.get("input_tokens"), usage.get("output_tokens"))
    blocks = data.get("content") or []
    calls = [{"id": b["id"], "name": b["name"], "args": b.get("input") or {}}
             for b in blocks if b.get("type") == "tool_use"]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return {"text": text, "calls": calls, "raw": {"content": blocks}}
