"""Owner directive §78: adapter usage normalization with schema-valid fixtures for
Anthropic / OpenAI / Gemini / xAI / Mistral / DeepSeek / OpenRouter. No live calls."""
from __future__ import annotations

import httpx
import pytest

from backend.providers.clients import (
    AnthropicClient,
    GeminiClient,
    OpenAICompatibleClient,
    normalize_anthropic_usage,
    normalize_gemini_usage,
    normalize_openai_usage,
)

# --- pure normalizer tests -------------------------------------------------

def test_openai_usage_with_details():
    """OpenAI + xAI shape: prompt/completion details with cached + reasoning tokens."""
    usage = {
        "prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
        "prompt_tokens_details": {"cached_tokens": 400},
        "completion_tokens_details": {"reasoning_tokens": 50},
    }
    n = normalize_openai_usage(usage)
    assert n["input_tokens"] == 1000
    assert n["cached_input_tokens"] == 400
    assert n["reasoning_tokens"] == 50
    assert n["total_tokens"] == 1200
    assert n["provider_cost"] is None


def test_mistral_usage_minimal():
    """Mistral reports plain prompt/completion/total — missing details stay None."""
    n = normalize_openai_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert n["input_tokens"] == 10 and n["output_tokens"] == 5
    assert n["cached_input_tokens"] is None
    assert n["reasoning_tokens"] is None


def test_deepseek_cache_hit_tokens():
    n = normalize_openai_usage({
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
        "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36,
    })
    assert n["cached_input_tokens"] == 64


def test_openrouter_reported_cost_preserved():
    """OpenRouter usage accounting includes authoritative cost — never replaced."""
    n = normalize_openai_usage({
        "prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600,
        "cost": 0.00234, "prompt_tokens_details": {"cached_tokens": 0},
    })
    assert n["provider_cost"] == 0.00234


def test_anthropic_usage_with_cache():
    n = normalize_anthropic_usage({
        "input_tokens": 800, "output_tokens": 150,
        "cache_read_input_tokens": 300, "cache_creation_input_tokens": 50,
    })
    assert n["input_tokens"] == 800
    assert n["cached_input_tokens"] == 300
    assert n["total_tokens"] == 950
    assert n["provider_cost"] is None


def test_gemini_usage_with_thoughts():
    n = normalize_gemini_usage({
        "promptTokenCount": 120, "candidatesTokenCount": 40,
        "cachedContentTokenCount": 30, "thoughtsTokenCount": 25, "totalTokenCount": 185,
    })
    assert n["input_tokens"] == 120
    assert n["reasoning_tokens"] == 25
    assert n["cached_input_tokens"] == 30


def test_absent_usage_is_all_none_never_zero():
    """§37: absent usage metadata means NULL, never assumed zero."""
    assert normalize_openai_usage(None) == {}
    assert normalize_anthropic_usage(None) == {}
    assert normalize_gemini_usage({}) == {
        "input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
        "reasoning_tokens": None, "total_tokens": None, "provider_cost": None,
    }


# --- full client round-trips against schema-valid fixture responses --------

@pytest.mark.parametrize("base_url,provider_label", [
    ("https://api.openai.com/v1", "openai"),
    ("https://api.x.ai/v1", "xai"),
    ("https://api.mistral.ai/v1", "mistral"),
    ("https://api.deepseek.com", "deepseek"),
    ("https://openrouter.ai/api/v1", "openrouter"),
])
async def test_openai_style_clients_normalize_once(monkeypatch, base_url, provider_label):
    """One request → one usage record shape; actual model + request id preserved;
    streaming is not used, so nothing can double count."""
    fixture = {
        "id": f"req-{provider_label}-1",
        "model": f"{provider_label}-actual-model",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
                  **({"cost": 0.001} if provider_label == "openrouter" else {})},
    }
    calls = []

    async def fake_post(self, url, json=None, headers=None):
        calls.append(json)
        return httpx.Response(200, json=fixture)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OpenAICompatibleClient("key", base_url, "requested-model")
    result = await client.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 1  # exactly one request — no double counting
    assert result.model == f"{provider_label}-actual-model"
    assert result.provider_request_id == f"req-{provider_label}-1"
    assert result.input_tokens == 11 and result.output_tokens == 7
    if provider_label == "openrouter":
        assert result.provider_cost == 0.001
        assert calls[0].get("usage") == {"include": True}  # accounting requested
    else:
        assert result.provider_cost is None


async def test_anthropic_client_roundtrip(monkeypatch):
    fixture = {
        "id": "msg_01", "model": "claude-sonnet-5-20260101",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 9, "output_tokens": 3, "cache_read_input_tokens": 4},
    }

    async def fake_post(self, url, json=None, headers=None):
        return httpx.Response(200, json=fixture)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = AnthropicClient("key")
    result = await client.complete([{"role": "user", "content": "hi"}])
    assert result.model == "claude-sonnet-5-20260101"
    assert result.cached_input_tokens == 4


async def test_gemini_client_roundtrip(monkeypatch):
    fixture = {
        "responseId": "resp-1", "modelVersion": "gemini-2.5-flash-002",
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "usageMetadata": {"promptTokenCount": 6, "candidatesTokenCount": 2,
                          "totalTokenCount": 8},
    }

    async def fake_post(self, url, json=None, headers=None):
        return httpx.Response(200, json=fixture)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = GeminiClient("key")
    result = await client.complete([{"role": "user", "content": "hi"}])
    assert result.model == "gemini-2.5-flash-002"
    assert result.input_tokens == 6 and result.output_tokens == 2


# --- pricing estimation determinism ---------------------------------------

async def test_estimated_cost_uses_snapshot(db_session, client, auth):
    from backend.providers.pricing import current_snapshot, estimate_cost

    snap = await current_snapshot(db_session, "openai", "gpt-4.1-mini")
    assert snap is not None
    # 1M non-cached input + 1M output at $0.40/$1.60
    cost = estimate_cost(snap, input_tokens=1_000_000, cached_input_tokens=0,
                         output_tokens=1_000_000)
    assert cost == pytest.approx(2.0)
    # cached tokens billed at cached rate
    cost2 = estimate_cost(snap, input_tokens=1_000_000, cached_input_tokens=1_000_000,
                          output_tokens=0)
    assert cost2 == pytest.approx(0.10)
    # no token data → None, never zero
    assert estimate_cost(snap, input_tokens=None, cached_input_tokens=None,
                         output_tokens=None) is None
