"""LLM provider clients.

All clients expose `complete(messages, ...) -> LlmResult`. Business logic never
instantiates these directly — go through ProviderRegistry (§29-31).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

DEFAULT_TIMEOUT = 60.0


@dataclass
class LlmResult:
    """Normalized generation result. None means the provider did not report the value —
    it is stored as NULL, never guessed (owner directive §28/§37)."""

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    model: str = ""                      # actual model reported by the provider
    provider_cost: float | None = None   # authoritative provider-reported cost (e.g. OpenRouter)
    provider_request_id: str | None = None
    raw: dict = field(default_factory=dict)

    def parse_json(self) -> dict | list | None:
        """Best-effort JSON extraction from a model reply (deterministic)."""
        text = self.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
        if start < 0:
            return None
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
        return None


class ProviderError(Exception):
    """Provider-side failure. status_code carries the HTTP status when the
    failure came from an HTTP response (None otherwise) — routing layers use
    it to decide whether a fallback attempt is worthwhile."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BaseLlmClient:
    provider_name = "base"

    def __init__(self, api_key: str, base_url: str | None = None, default_model: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LlmResult:
        raise NotImplementedError

    async def health_check(self) -> bool:
        raise NotImplementedError


class OpenAICompatibleClient(BaseLlmClient):
    """OpenAI, xAI/Grok, and any OpenAI-compatible endpoint (chat completions)."""

    provider_name = "openai_compatible"

    def __init__(self, api_key: str, base_url: str | None = None, default_model: str = "gpt-4.1-mini"):
        super().__init__(api_key, base_url or "https://api.openai.com/v1", default_model)

    async def complete(self, messages, *, model=None, max_tokens=1024, temperature=0.3, json_mode=False) -> LlmResult:
        payload: dict = {
            "model": model or self.default_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if "openrouter.ai" in (self.base_url or ""):
            # ask OpenRouter to include authoritative usage + cost accounting
            payload["usage"] = {"include": True}
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        if resp.status_code != 200:
            raise ProviderError(f"provider returned {resp.status_code}", status_code=resp.status_code)
        data = resp.json()
        return LlmResult(
            text=data["choices"][0]["message"].get("content") or "",
            model=data.get("model", payload["model"]),
            provider_request_id=data.get("id"),
            **normalize_openai_usage(data.get("usage")),
        )

    async def health_check(self) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return resp.status_code == 200


class AnthropicClient(BaseLlmClient):
    provider_name = "anthropic"

    def __init__(self, api_key: str, base_url: str | None = None, default_model: str = "claude-sonnet-5"):
        super().__init__(api_key, base_url or "https://api.anthropic.com", default_model)

    async def complete(self, messages, *, model=None, max_tokens=1024, temperature=0.3, json_mode=False) -> LlmResult:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        chat = [m for m in messages if m["role"] != "system"]
        if json_mode and system_parts:
            system_parts.append("Respond with a single valid JSON object and nothing else.")
        payload: dict = {
            "model": model or self.default_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json=payload,
            )
        if resp.status_code != 200:
            raise ProviderError(f"provider returned {resp.status_code}", status_code=resp.status_code)
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return LlmResult(
            text=text,
            model=data.get("model", payload["model"]),
            provider_request_id=data.get("id"),
            **normalize_anthropic_usage(data.get("usage")),
        )

    async def health_check(self) -> bool:
        try:
            await self.complete([{"role": "user", "content": "ping"}], max_tokens=1)
            return True
        except Exception:
            return False


class MockLlmClient(BaseLlmClient):
    """Deterministic mock for tests/demo (no external calls, no secrets).

    Responses can be scripted via configuration_json {"responses": {substring: reply}}.
    """

    provider_name = "mock"

    def __init__(self, api_key: str = "mock", base_url: str | None = None, default_model: str = "mock-1",
                 scripted: dict[str, str] | None = None):
        super().__init__(api_key, base_url, default_model)
        self.scripted = scripted or {}

    async def complete(self, messages, *, model=None, max_tokens=1024, temperature=0.3, json_mode=False) -> LlmResult:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        for needle, reply in self.scripted.items():
            if needle.lower() in last_user.lower():
                return LlmResult(
                    text=reply, input_tokens=len(last_user) // 4,
                    output_tokens=len(reply) // 4, model="mock-1",
                )
        reply = json.dumps({"mock": True}) if json_mode else f"[mock] Received: {last_user[:200]}"
        return LlmResult(text=reply, input_tokens=len(last_user) // 4,
                         output_tokens=len(reply) // 4, model="mock-1")

    async def health_check(self) -> bool:
        return True


def _maybe_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_openai_usage(usage: dict | None) -> dict:
    """OpenAI-compatible usage (OpenAI, xAI, Mistral, DeepSeek, OpenRouter, custom).
    Missing values stay None. OpenRouter's authoritative `cost` is preserved."""
    if not isinstance(usage, dict):
        return {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    cached = _maybe_int(prompt_details.get("cached_tokens"))
    if cached is None:  # DeepSeek reports prompt_cache_hit_tokens
        cached = _maybe_int(usage.get("prompt_cache_hit_tokens"))
    cost = usage.get("cost")
    return {
        "input_tokens": _maybe_int(usage.get("prompt_tokens")),
        "output_tokens": _maybe_int(usage.get("completion_tokens")),
        "cached_input_tokens": cached,
        "reasoning_tokens": _maybe_int(completion_details.get("reasoning_tokens")),
        "total_tokens": _maybe_int(usage.get("total_tokens")),
        "provider_cost": float(cost) if isinstance(cost, (int, float)) else None,
    }


def normalize_anthropic_usage(usage: dict | None) -> dict:
    if not isinstance(usage, dict):
        return {}
    input_t = _maybe_int(usage.get("input_tokens"))
    output_t = _maybe_int(usage.get("output_tokens"))
    cached = _maybe_int(usage.get("cache_read_input_tokens"))
    total = input_t + output_t if input_t is not None and output_t is not None else None
    return {
        "input_tokens": input_t,
        "output_tokens": output_t,
        "cached_input_tokens": cached,
        "reasoning_tokens": None,  # anthropic thinking tokens are billed as output
        "total_tokens": total,
        "provider_cost": None,
    }


def normalize_gemini_usage(usage: dict | None) -> dict:
    if not isinstance(usage, dict):
        return {}
    return {
        "input_tokens": _maybe_int(usage.get("promptTokenCount")),
        "output_tokens": _maybe_int(usage.get("candidatesTokenCount")),
        "cached_input_tokens": _maybe_int(usage.get("cachedContentTokenCount")),
        "reasoning_tokens": _maybe_int(usage.get("thoughtsTokenCount")),
        "total_tokens": _maybe_int(usage.get("totalTokenCount")),
        "provider_cost": None,
    }


class GeminiClient(BaseLlmClient):
    """Google Gemini via the official generateContent REST API."""

    provider_name = "gemini"

    def __init__(self, api_key: str, base_url: str | None = None,
                 default_model: str = "gemini-2.5-flash"):
        super().__init__(api_key, base_url or "https://generativelanguage.googleapis.com/v1beta",
                         default_model)

    async def complete(self, messages, *, model=None, max_tokens=1024,
                       temperature=0.3, json_mode=False) -> LlmResult:
        target = model or self.default_model
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        contents = [
            {"role": "user" if m["role"] == "user" else "model",
             "parts": [{"text": m["content"]}]}
            for m in messages if m["role"] in ("user", "assistant")
        ]
        payload: dict = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/models/{target}:generateContent",
                headers={"x-goog-api-key": self.api_key},
                json=payload,
            )
        if resp.status_code != 200:
            raise ProviderError(f"provider returned {resp.status_code}", status_code=resp.status_code)
        data = resp.json()
        candidates = data.get("candidates") or []
        text = ""
        if candidates:
            text = "".join(p.get("text", "")
                           for p in (candidates[0].get("content") or {}).get("parts", []))
        return LlmResult(
            text=text,
            model=data.get("modelVersion", target),
            provider_request_id=data.get("responseId"),
            **normalize_gemini_usage(data.get("usageMetadata")),
        )

    async def health_check(self) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/models",
                                    headers={"x-goog-api-key": self.api_key})
        return resp.status_code == 200
