"""Phase 4 acceptance (§119, §137): Tool Broker permission enforcement, adapters,
no-secret-leak invariants."""
from __future__ import annotations

import pytest

from backend.core import killswitch
from backend.integrations import broker
from backend.policies import engine as policy
from tests.conftest import auth_headers


async def create_demo_connection(client, headers, capabilities=None):
    """Gmail-shaped fixture data for the pipeline tests.

    The product no longer offers demo data and POST /integrations refuses to
    create it, so this seeds the row directly. The synthetic adapter still reads
    inside the test environment only — see broker.SYNTHETIC_TYPES.
    """
    from backend.core.db import get_sessionmaker
    from backend.core.models import IntegrationConnection

    me = (await client.get("/api/me", headers=headers)).json()
    async with get_sessionmaker()() as db:
        conn = IntegrationConnection(
            user_id=me["id"], integration_type="demo", name="Demo Google",
            capabilities_json=capabilities or {"gmail.read": "READ",
                                               "calendar.read": "READ"})
        db.add(conn)
        await db.commit()
        return {"id": conn.id, "integration_type": conn.integration_type,
                "name": conn.name, "capabilities": conn.capabilities_json}


async def test_demo_gmail_read_through_broker(client, auth, db_session):
    await create_demo_connection(client, auth)
    me = (await client.get("/api/me", headers=auth)).json()
    result = await broker.invoke(db_session, me["id"], "gmail.read", "gmail.get_all_messages")
    assert result["synthetic"] is True
    assert len(result["messages"]) > 20


async def test_capability_denied_without_grant(client, auth, db_session):
    await create_demo_connection(client, auth, {"gmail.read": "DENIED"})
    me = (await client.get("/api/me", headers=auth)).json()
    with pytest.raises(policy.PolicyDenied):
        await broker.invoke(db_session, me["id"], "gmail.read", "gmail.get_all_messages")
    # denial creates a ledger event
    acts = (await client.get("/api/activity", headers=auth)).json()
    assert any(e["event_type"] == "tool_denied" for e in acts)


async def test_execute_requires_execute_grant(client, auth, db_session):
    """READ grant must not allow EXECUTE-level capabilities (webhook.execute)."""
    resp = await client.post("/api/integrations", json={
        "integration_type": "webhook", "name": "Zap",
        "configuration": {"url": "http://localhost:9/never-called"},
        "capabilities": {"webhook.execute": "READ"},
    }, headers=auth)
    conn = resp.json()
    me = (await client.get("/api/me", headers=auth)).json()
    with pytest.raises(policy.PolicyDenied):
        await broker.invoke(db_session, me["id"], "webhook.execute", "trigger",
                            {"payload": {}}, connection_id=conn["id"])


async def test_external_actions_kill_switch(client, auth, db_session):
    resp = await client.post("/api/integrations", json={
        "integration_type": "webhook", "name": "Zap",
        "configuration": {"url": "http://localhost:9/never-called"},
        "capabilities": {"webhook.execute": "EXECUTE"},
    }, headers=auth)
    conn = resp.json()
    me = (await client.get("/api/me", headers=auth)).json()
    await client.post("/api/settings/kill-switch",
                      json={"switch": "disable_external_actions", "on": True}, headers=auth)
    with pytest.raises(killswitch.KillSwitchEngaged):
        await broker.invoke(db_session, me["id"], "webhook.execute", "trigger",
                            {"payload": {}}, connection_id=conn["id"])


async def test_grant_endpoint_and_invoke_api(client, auth):
    conn = await create_demo_connection(client, auth, {"gmail.read": "DENIED"})
    resp = await client.post("/api/integrations/invoke", json={
        "capability": "gmail.read", "operation": "gmail.get_all_messages",
    }, headers=auth)
    assert resp.status_code == 403
    await client.post(f"/api/integrations/{conn['id']}/grant",
                      json={"capability": "gmail.read", "level": "READ"}, headers=auth)
    resp = await client.post("/api/integrations/invoke", json={
        "capability": "gmail.read", "operation": "gmail.get_all_messages",
    }, headers=auth)
    assert resp.status_code == 200
    assert resp.json()["result"]["synthetic"] is True


async def test_integration_secrets_never_serialized(client, auth):
    await client.post("/api/integrations", json={
        "integration_type": "webhook", "name": "Hook",
        "configuration": {"url": "https://example.com/hook"},
        "secret_headers": {"Authorization": "Bearer super-secret-hook-token"},
    }, headers=auth)
    listing = await client.get("/api/integrations", headers=auth)
    assert "super-secret-hook-token" not in listing.text
    assert listing.json()[0]["has_credentials"] is True


async def test_integration_tenancy(client):
    h_a = await auth_headers(client, "ia@example.com")
    h_b = await auth_headers(client, "ib@example.com")
    conn = await create_demo_connection(client, h_a)
    assert (await client.get("/api/integrations", headers=h_b)).json() == []
    resp = await client.post(f"/api/integrations/{conn['id']}/grant",
                             json={"capability": "gmail.read", "level": "EXECUTE"}, headers=h_b)
    assert resp.status_code == 404
    resp = await client.delete(f"/api/integrations/{conn['id']}", headers=h_b)
    assert resp.status_code == 404


async def test_disconnect_and_purge(client, auth):
    conn = await create_demo_connection(client, auth)
    resp = await client.delete(f"/api/integrations/{conn['id']}", params={"purge": "true"}, headers=auth)
    assert resp.json()["ok"] is True
    acts = (await client.get("/api/activity", headers=auth)).json()
    assert any(e["event_type"] == "integration_disconnected" for e in acts)
    assert any(e["event_type"] == "data_purged" for e in acts)


async def test_mcp_adapter_parses_jsonrpc(db_session, client, auth, monkeypatch):
    """MCP client speaks JSON-RPC and extracts SSE data lines."""
    import httpx

    from backend.core.models import IntegrationConnection
    from backend.integrations.mcp.client import McpAdapter

    me = (await client.get("/api/me", headers=auth)).json()
    conn = IntegrationConnection(user_id=me["id"], integration_type="mcp", name="Test MCP",
                                 configuration_json={"url": "http://mcp.test/rpc"})

    calls = []

    async def fake_post(self, url, json=None, headers=None):
        calls.append(json)
        method = json.get("method")
        if method == "initialize":
            body = {"jsonrpc": "2.0", "id": json["id"],
                    "result": {"protocolVersion": "2025-03-26", "serverInfo": {"name": "t"}}}
        elif method == "tools/list":
            body = {"jsonrpc": "2.0", "id": json["id"],
                    "result": {"tools": [{"name": "search", "description": "Search things"}]}}
        elif method == "tools/call":
            body = {"jsonrpc": "2.0", "id": json["id"],
                    "result": {"content": [{"type": "text", "text": "42"}]}}
        else:
            body = {"jsonrpc": "2.0", "id": json.get("id"), "result": {}}
        import json as jsonlib
        return httpx.Response(200, text=f"event: message\ndata: {jsonlib.dumps(body)}\n\n")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    adapter = McpAdapter(conn)
    tools = await adapter.read("tools.list", {})
    assert tools["tools"][0]["name"] == "search"
    result = await adapter.execute("tools.call", {"name": "search", "arguments": {"q": "x"}})
    assert result["content"][0]["text"] == "42"
