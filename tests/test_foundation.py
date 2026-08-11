"""Phase 1 acceptance: auth, tenancy, providers, disable semantics, kill switches, ledger."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.core import killswitch
from backend.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from backend.core.models import Event
from backend.ledger import service as ledger
from backend.providers import registry
from tests.conftest import auth_headers, setup_mock_provider


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_login_and_me(client):
    headers = await auth_headers(client, "alice@example.com")
    resp = await client.get("/api/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["autonomy_mode"] == "assisted"


async def test_me_requires_auth(client):
    assert (await client.get("/api/me")).status_code == 401
    resp = await client.get("/api/me", headers={"Authorization": "Bearer notatoken"})
    assert resp.status_code == 401


async def test_crypto_roundtrip():
    secret = "sk-super-secret-key-4821"
    token = encrypt_secret(secret)
    assert secret not in token
    assert decrypt_secret(token) == secret
    assert mask_secret(secret) == "sk-…4821"


async def test_provider_secret_never_returned(client, auth):
    resp = await client.post(
        "/api/providers",
        json={"provider": "openai", "api_key": "sk-test-1234567890abcdef"},
        headers=auth,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "sk-test-1234567890abcdef" not in str(body)
    assert body["display_hint"].endswith("cdef")
    listing = await client.get("/api/providers", headers=auth)
    assert "sk-test-1234567890abcdef" not in listing.text


async def test_disabled_provider_never_called(client, auth, db_session):
    await setup_mock_provider(client, auth)
    me = (await client.get("/api/me", headers=auth)).json()

    # enabled: resolves
    client_obj, row = await registry.resolve_client(db_session, me["id"], "chat")
    assert row.provider == "mock"

    # disable via API → resolve must fail
    resp = await client.post("/api/providers/mock/toggle", json={"enabled": False}, headers=auth)
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    db_session.expire_all()
    with pytest.raises(registry.NoProviderAvailable):
        await registry.resolve_client(db_session, me["id"], "chat")


async def test_llm_kill_switch_blocks_calls(client, auth, db_session):
    await setup_mock_provider(client, auth)
    me = (await client.get("/api/me", headers=auth)).json()
    resp = await client.post(
        "/api/settings/kill-switch", json={"switch": "disable_llm", "on": True}, headers=auth
    )
    assert resp.status_code == 200
    with pytest.raises(killswitch.KillSwitchEngaged):
        await registry.complete(db_session, me["id"], "chat", [{"role": "user", "content": "hi"}])


async def test_tenant_isolation_providers(client):
    h_a = await auth_headers(client, "a@example.com")
    h_b = await auth_headers(client, "b@example.com")
    await client.post("/api/providers", json={"provider": "openai", "api_key": "sk-user-a-key-1111"}, headers=h_a)
    listing_b = (await client.get("/api/providers", headers=h_b)).json()
    assert listing_b == []
    # user B cannot toggle A's provider
    resp = await client.post("/api/providers/openai/toggle", json={"enabled": False}, headers=h_b)
    assert resp.status_code == 404


async def test_ledger_append_only(db_session, client, auth):
    me = (await client.get("/api/me", headers=auth)).json()
    ev = await ledger.record(db_session, me["id"], "goal_created", payload={"x": 1})
    await db_session.commit()
    ev = (await db_session.execute(select(Event).where(Event.id == ev.id))).scalar_one()
    ev.event_type = "goal_updated"
    with pytest.raises(RuntimeError, match="append-only"):
        await db_session.commit()
    await db_session.rollback()
    with pytest.raises(RuntimeError, match="append-only"):
        await db_session.delete(ev)
        await db_session.commit()
    await db_session.rollback()


async def test_activity_endpoint_and_tenancy(client):
    h_a = await auth_headers(client, "a2@example.com")
    h_b = await auth_headers(client, "b2@example.com")
    await client.post("/api/providers", json={"provider": "mock", "api_key": "mock"}, headers=h_a)
    acts_a = (await client.get("/api/activity", headers=h_a)).json()
    assert any(e["event_type"] == "provider_connected" for e in acts_a)
    acts_b = (await client.get("/api/activity", headers=h_b)).json()
    assert acts_b == []


async def test_kill_switch_listing(client, auth):
    resp = await client.get("/api/settings", headers=auth)
    ks = resp.json()["kill_switches"]
    assert set(ks) == set(killswitch.ALL_SWITCHES)
    assert not any(ks.values())


async def test_mock_llm_complete_records_usage(client, auth, db_session):
    await setup_mock_provider(client, auth, {"ping": "pong"})
    me = (await client.get("/api/me", headers=auth)).json()
    result = await registry.complete(db_session, me["id"], "chat", [{"role": "user", "content": "ping"}])
    assert result.text == "pong"
    from backend.core.models import LlmUsage

    usage = (await db_session.execute(select(LlmUsage))).scalars().all()
    assert len(usage) == 1 and usage[0].purpose == "chat"


async def test_provider_definitions_never_empty(client, auth):
    """Third pass §1: the provider selector source must list every supported
    provider for a fresh user, with truthful connection state."""
    defs = (await client.get("/api/providers/definitions", headers=auth)).json()
    ids = [d["id"] for d in defs]
    for expected in ("anthropic", "openai", "gemini", "xai", "mistral",
                     "deepseek", "openrouter", "custom", "mock"):
        assert expected in ids
    # every definition id is actually accepted by the backend
    assert set(ids) <= registry.KNOWN_PROVIDERS
    # fresh user: everything not connected
    assert all(d["state"] == "not_connected" for d in defs)

    # connect one → state flips; disable → disabled
    await client.post("/api/providers", json={"provider": "mock", "api_key": "mock"}, headers=auth)
    defs = (await client.get("/api/providers/definitions", headers=auth)).json()
    assert next(d for d in defs if d["id"] == "mock")["state"] == "connected"
    await client.post("/api/providers/mock/toggle", json={"enabled": False}, headers=auth)
    defs = (await client.get("/api/providers/definitions", headers=auth)).json()
    assert next(d for d in defs if d["id"] == "mock")["state"] == "disabled"
