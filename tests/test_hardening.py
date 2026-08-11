"""Phase 15 hardening (§129, §132): secret-leak sweep, provider fallback, LLM budgets,
chat tenancy."""
from __future__ import annotations

import json

import pytest

from tests.conftest import auth_headers, setup_mock_provider

SECRETS = {
    "provider_key": "sk-ultra-secret-provider-key-777",
    "webhook_token": "whk-ultra-secret-hook-888",
    "agent_credential": "agent-ultra-secret-cred-999",
    "byos_secret": "s3-ultra-secret-key-000",
}


async def test_no_secret_leaks_across_api_surface(client, auth):
    """Store secrets in every subsystem, then sweep every read endpoint for leakage (§38)."""
    await client.post("/api/providers",
                      json={"provider": "openai", "api_key": SECRETS["provider_key"]}, headers=auth)
    await client.post("/api/providers/openai/toggle", json={"enabled": False}, headers=auth)
    await client.post("/api/integrations", json={
        "integration_type": "webhook", "name": "Hook",
        "configuration": {"url": "https://example.com/h"},
        "secret_headers": {"Authorization": SECRETS["webhook_token"]},
    }, headers=auth)
    await client.post("/api/integrations", json={
        "integration_type": "s3", "name": "Bucket",
        "configuration": {"bucket": "b"},
        "secret_headers": {"secret_access_key": SECRETS["byos_secret"]},
    }, headers=auth)
    await client.post("/api/agents", json={
        "adapter_type": "custom_http", "display_name": "Ext",
        "configuration": {"endpoint": "http://x.test"},
        "credential": SECRETS["agent_credential"],
    }, headers=auth)

    read_endpoints = [
        "/api/me", "/api/providers", "/api/integrations", "/api/agents", "/api/settings",
        "/api/activity", "/api/goals", "/api/documents", "/api/documents/export",
        "/api/chat/messages", "/api/today", "/api/treasury", "/api/spend-intents",
        "/api/approvals", "/api/opportunities", "/api/experiments", "/api/xray/latest",
        "/api/files", "/api/market/signals", "/api/decisions", "/api/predictions",
        "/api/telegram/binding",
    ]
    for endpoint in read_endpoints:
        resp = await client.get(endpoint, headers=auth)
        assert resp.status_code == 200, f"{endpoint}: {resp.status_code}"
        body = resp.text
        for name, secret in SECRETS.items():
            assert secret not in body, f"{name} leaked via {endpoint}"


async def test_provider_disable_falls_back(client, auth, db_session):
    """§132: disabled provider is never used; routing falls through to the next one."""
    me = (await client.get("/api/me", headers=auth)).json()
    await client.post("/api/providers",
                      json={"provider": "openai", "api_key": "sk-disabled-key"}, headers=auth)
    await setup_mock_provider(client, auth, {"hello": "fallback works"})
    await client.post("/api/providers/openai/toggle", json={"enabled": False}, headers=auth)

    from backend.providers import registry

    result = await registry.complete(db_session, me["id"], "chat",
                                     [{"role": "user", "content": "hello"}])
    assert result.text == "fallback works"
    # usage was attributed to the mock provider, never the disabled one
    from sqlalchemy import select

    from backend.core.models import LlmUsage

    rows = (await db_session.execute(select(LlmUsage))).scalars().all()
    assert all(r.provider == "mock" for r in rows)


async def test_llm_budget_deterministic(client, auth, db_session):
    """§32: LLM budgets enforced in code; exceeded budget blocks further calls."""
    me = (await client.get("/api/me", headers=auth)).json()
    await setup_mock_provider(client, auth, {"x": "y" * 400})

    from backend.core.models import Budget
    from backend.providers import registry

    db_session.add(Budget(user_id=me["id"], scope="llm", daily_limit_cents=0))
    await db_session.commit()
    with pytest.raises(registry.LlmBudgetExceeded):
        await registry.complete(db_session, me["id"], "chat", [{"role": "user", "content": "x"}])


async def test_chat_session_tenancy(client):
    h_a = await auth_headers(client, "chata@example.com")
    h_b = await auth_headers(client, "chatb@example.com")
    await setup_mock_provider(client, h_a, {"private": "reply-a"})
    await client.post("/api/chat/message", json={"text": "private message alpha"}, headers=h_a)
    messages_b = (await client.get("/api/chat/messages", headers=h_b)).json()
    assert messages_b == []
    assert "alpha" not in json.dumps(messages_b)


async def test_ledger_has_no_mutation_endpoints(client, auth):
    """§141: ordinary APIs cannot rewrite history — no update/delete routes exist."""
    acts = (await client.get("/api/activity", headers=auth)).json()
    # even with no events, mutation routes must not exist
    for method, path in [("PATCH", "/api/activity/x"), ("DELETE", "/api/activity/x"),
                         ("PUT", "/api/activity/x")]:
        resp = await client.request(method, path, headers=auth)
        assert resp.status_code in (404, 405), (method, path, resp.status_code)
    assert isinstance(acts, list)


async def test_unverified_user_can_login_but_is_tracked(client, auth):
    me = (await client.get("/api/me", headers=auth)).json()
    assert me["is_verified"] is False  # verification exists; enforcement is a config choice
