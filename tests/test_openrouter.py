"""Owner directive §77: OpenRouter first-class acceptance."""
from __future__ import annotations

import httpx
import pytest

OPENROUTER_MODELS = {
    "data": [
        {"id": "anthropic/claude-sonnet-5", "name": "Anthropic: Claude Sonnet 5",
         "context_length": 200000, "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
        {"id": "openai/gpt-4.1-mini", "name": "OpenAI: GPT-4.1 Mini",
         "context_length": 1000000, "pricing": {"prompt": "0.0000004", "completion": "0.0000016"}},
        {"id": "qwen/qwen3-embedding", "name": "Qwen Embedding", "context_length": 32000},
    ]
}

GENERATION_FIXTURE = {
    "id": "gen-abc123",
    "model": "anthropic/claude-sonnet-5-20260101",  # actual returned model differs
    "choices": [{"message": {"role": "assistant", "content": "ready"}}],
    "usage": {"prompt_tokens": 42, "completion_tokens": 5, "total_tokens": 47,
              "cost": 0.000186, "prompt_tokens_details": {"cached_tokens": 12}},
}


async def test_openrouter_full_acceptance(client, auth, db_session, monkeypatch):
    # 1-2: save key → encrypted persistence, never returned
    resp = await client.post("/api/providers", json={
        "provider": "openrouter", "api_key": "sk-or-v1-secret-key-9876",
    }, headers=auth)
    assert resp.status_code == 200
    assert "sk-or-v1-secret-key-9876" not in resp.text
    listing = await client.get("/api/providers", headers=auth)
    assert "sk-or-v1-secret-key-9876" not in listing.text

    orig_get, orig_post = httpx.AsyncClient.get, httpx.AsyncClient.post

    async def fake_get(self, url, **kwargs):
        if "openrouter.ai" in str(url):
            return httpx.Response(200, json=OPENROUTER_MODELS)
        return await orig_get(self, url, **kwargs)

    async def fake_post(self, url, **kwargs):
        if "openrouter.ai" in str(url):
            assert kwargs.get("json", {}).get("usage") == {"include": True}
            return httpx.Response(200, json=GENERATION_FIXTURE)
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    # 3: test connection (health = GET /models)
    resp = await client.post("/api/providers/openrouter/test", headers=auth)
    assert resp.json()["ok"] is True

    # 4: fetch model catalog (live discovery)
    resp = await client.post("/api/providers/openrouter/models/refresh", headers=auth)
    assert resp.json() == {"provider": "openrouter", "source": "live", "count": 3,
                           "error": None}

    # 5: search the catalog; embeddings filtered out of reasoning selectors
    models = (await client.get("/api/providers/openrouter/models",
                               params={"search": "claude"}, headers=auth)).json()
    assert [m["model_id"] for m in models] == ["anthropic/claude-sonnet-5"]
    all_models = (await client.get("/api/providers/openrouter/models", headers=auth)).json()
    assert all(m["model_id"] != "qwen/qwen3-embedding" for m in all_models)

    # 6-7: select a compatible model as Orchestrator
    resp = await client.put("/api/orchestrator", json={
        "provider": "openrouter", "model": "anthropic/claude-sonnet-5",
    }, headers=auth)
    assert resp.status_code == 200

    # 8-12: execute → requested + actual model + usage + provider-reported cost stored
    me = (await client.get("/api/me", headers=auth)).json()
    from backend.providers import registry

    result = await registry.generate(db_session, me["id"],
                                     [{"role": "user", "content": "ping"}],
                                     crew_role="orchestrator")
    await db_session.commit()
    assert result.text == "ready"

    events = (await client.get("/api/usage/events", headers=auth)).json()
    ev = events[0]
    assert ev["provider"] == "openrouter"
    assert ev["requested_model"] == "anthropic/claude-sonnet-5"
    assert ev["actual_model"] == "anthropic/claude-sonnet-5-20260101"
    assert ev["input_tokens"] == 42 and ev["cached_input_tokens"] == 12
    assert ev["provider_reported_cost"] == 0.000186
    assert ev["cost_source"] == "PROVIDER_REPORTED"

    # 13: truthful usage summary
    summary = (await client.get("/api/usage/summary", headers=auth)).json()
    assert summary["month"]["reported_cost"] == 0.000186

    # 14-15: disable OpenRouter → next request CANNOT use it
    await client.post("/api/providers/openrouter/toggle", json={"enabled": False},
                      headers=auth)
    db_session.expire_all()
    with pytest.raises(registry.NoProviderAvailable):
        await registry.generate(db_session, me["id"], [{"role": "user", "content": "x"}],
                                crew_role="orchestrator")
