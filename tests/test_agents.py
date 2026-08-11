"""Phase 9 acceptance (§124, §137): agent switching preserves platform-owned state;
adapters get sanitized DTOs without secrets."""
from __future__ import annotations

import httpx

from backend.agents import router as agent_router
from tests.conftest import setup_mock_provider


async def test_default_native_agent(client, auth):
    agents = (await client.get("/api/agents", headers=auth)).json()
    assert len(agents) == 1
    assert agents[0]["adapter_type"] == "native"
    assert agents[0]["is_active"] is True


async def test_hermes_blocked_with_message(client, auth):
    resp = await client.post("/api/agents", json={
        "adapter_type": "hermes", "display_name": "Hermes",
    }, headers=auth)
    assert resp.status_code == 400
    assert "custom_http" in resp.json()["detail"]


async def test_create_activate_switch_preserves_state(client, auth, db_session, monkeypatch):
    """Switching agents routes the next interaction elsewhere while goals/memory stay."""
    import json

    goal_json = json.dumps({
        "metric": "monthly_independent_income", "title": "Income", "target": 7000,
        "unit": "EUR", "currency": "EUR", "deadline": None, "constraints": {},
        "missing_critical": [],
    })
    await setup_mock_provider(client, auth, {"7000": goal_json})
    await client.post("/api/goals/compile", json={"text": "income 7000 monthly"}, headers=auth)

    resp = await client.post("/api/agents", json={
        "adapter_type": "custom_http", "display_name": "My External Agent",
        "configuration": {"endpoint": "http://agent.test/hook"},
        "credential": "Bearer custom-agent-secret-token",
    }, headers=auth)
    agent = resp.json()
    assert agent["has_credentials"] is True
    assert "custom-agent-secret-token" not in str(agent)

    await client.post(f"/api/agents/{agent['id']}/activate", headers=auth)
    agents = (await client.get("/api/agents", headers=auth)).json()
    active = [a for a in agents if a["is_active"]]
    assert len(active) == 1 and active[0]["adapter_type"] == "custom_http"

    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return httpx.Response(200, json={"reply": "External agent says hi"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    me = (await client.get("/api/me", headers=auth)).json()
    from sqlalchemy import select

    from backend.core.models import User

    user = (await db_session.execute(select(User).where(User.id == me["id"]))).scalar_one()
    reply = await agent_router.route_message(db_session, user, "status update please", channel="web")
    await db_session.commit()
    assert reply == "External agent says hi"

    # sanitized context: goals present, secrets absent (§137)
    payload_text = str(captured["payload"])
    assert "monthly_independent_income" in payload_text or "Income" in payload_text
    assert "custom-agent-secret-token" not in payload_text
    assert "sk-" not in payload_text
    # auth header used for transport only
    assert captured["headers"]["Authorization"] == "Bearer custom-agent-secret-token"

    # the platform still owns state: goals unchanged after switching back
    goals = (await client.get("/api/goals", headers=auth)).json()
    assert goals[0]["target_value"] == 7000


async def test_failed_external_agent_falls_back_to_native(client, auth, db_session, monkeypatch):
    await setup_mock_provider(client, auth, {"hello": "native fallback reply"})
    resp = await client.post("/api/agents", json={
        "adapter_type": "custom_http", "display_name": "Broken Agent",
        "configuration": {"endpoint": "http://agent.test/hook"},
    }, headers=auth)
    await client.post(f"/api/agents/{resp.json()['id']}/activate", headers=auth)

    async def fail_post(self, url, json=None, headers=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)
    me = (await client.get("/api/me", headers=auth)).json()
    from sqlalchemy import select

    from backend.core.models import User

    user = (await db_session.execute(select(User).where(User.id == me["id"]))).scalar_one()
    reply = await agent_router.route_message(db_session, user, "hello", channel="web")
    assert reply == "native fallback reply"


async def test_openclaw_adapter_payload(client, auth, db_session, monkeypatch):
    resp = await client.post("/api/agents", json={
        "adapter_type": "openclaw", "display_name": "OpenClaw",
        "configuration": {"base_url": "http://localhost:18789"},
        "credential": "oc-gateway-token",
    }, headers=auth)
    await client.post(f"/api/agents/{resp.json()['id']}/activate", headers=auth)

    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "claw reply"}}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    me = (await client.get("/api/me", headers=auth)).json()
    from sqlalchemy import select

    from backend.core.models import User

    user = (await db_session.execute(select(User).where(User.id == me["id"]))).scalar_one()
    reply = await agent_router.route_message(db_session, user, "ping", channel="web")
    assert reply == "claw reply"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer oc-gateway-token"


async def test_native_agent_cannot_be_deleted(client, auth):
    agents = (await client.get("/api/agents", headers=auth)).json()
    native = agents[0]
    resp = await client.delete(f"/api/agents/{native['id']}", headers=auth)
    assert resp.status_code == 400


async def test_duplicate_default_natives_self_heal(client, auth, db_session):
    """Two concurrent first requests can both insert the default native agent;
    the registry must collapse the duplicates on the next pass."""
    me = (await client.get("/api/me", headers=auth)).json()
    from backend.agents import registry
    from backend.core.models import AgentConfig

    await registry.ensure_default_agents(db_session, me["id"])
    db_session.add(AgentConfig(user_id=me["id"], adapter_type="native",
                               display_name="Native Agent", enabled=True,
                               is_active=True, health_status="ok"))
    await db_session.commit()

    agents = await registry.list_agents(db_session, me["id"])
    await db_session.commit()
    natives = [a for a in agents if a.adapter_type == "native"]
    assert len(natives) == 1
    assert sum(1 for a in agents if a.is_active) == 1
