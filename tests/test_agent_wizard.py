"""Create-agent wizard backend: avatar + role on an AgentConfig, through the
existing POST /api/agents route and the existing entitlement gates."""
from __future__ import annotations

from backend.core.config import get_settings
from backend.core.models import SubscriptionState


def _hosted(monkeypatch):
    """Stripe configured → Pro-only crew roles are actually gated."""
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_wizard")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")


async def _subscribe(client, auth, db_session, price_id: str):
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    db_session.add(SubscriptionState(user_id=uid, status="active", price_id=price_id))
    await db_session.commit()


# ── avatar catalogue ────────────────────────────────────────────────

async def test_avatar_catalogue_lists_the_shipped_illustrations(client, auth):
    body = (await client.get("/api/agents/avatars", headers=auth)).json()
    assert "crew-radar.webp" in body["avatars"]
    assert len(body["avatars"]) == 10
    assert all(a.startswith("crew-") and a.endswith(".webp") for a in body["avatars"])


# ── happy path ──────────────────────────────────────────────────────

async def test_create_native_agent_with_avatar_and_role(client, auth):
    resp = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "Radar",
        "configuration": {"avatar": "crew-radar.webp", "role": "radar"},
    })
    assert resp.status_code == 200, resp.text
    agent = resp.json()
    assert agent["adapter_type"] == "native"
    assert agent["configuration"]["avatar"] == "crew-radar.webp"
    assert agent["configuration"]["role"] == "radar"

    # it shows up in the crew list with its avatar, ready to render
    agents = (await client.get("/api/agents", headers=auth)).json()
    mine = [a for a in agents if a["display_name"] == "Radar"]
    assert len(mine) == 1 and mine[0]["configuration"]["avatar"] == "crew-radar.webp"


async def test_create_custom_http_agent_keeps_its_secret(client, auth):
    resp = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "custom_http", "display_name": "My Worker",
        "configuration": {"avatar": "crew-dev.webp", "endpoint": "https://agent.example/run",
                          "auth_header": "Authorization"},
        "credential": "super-secret-token",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_credentials"] is True
    assert "super-secret-token" not in resp.text          # never echoed back


# ── validation ──────────────────────────────────────────────────────

async def test_unknown_avatar_and_role_are_refused(client, auth):
    bad_avatar = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "X",
        "configuration": {"avatar": "../../etc/passwd"}})
    assert bad_avatar.status_code == 400 and "avatar" in bad_avatar.json()["detail"]

    bad_role = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "X",
        "configuration": {"role": "supreme-leader"}})
    assert bad_role.status_code == 400 and "role" in bad_role.json()["detail"]

    blank = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "   "})
    assert blank.status_code == 400

    bad_type = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "hermes", "display_name": "H"})
    assert bad_type.status_code == 400


# ── the wizard cannot bypass the plan ───────────────────────────────

async def test_pro_only_role_is_gated_for_basic(client, auth, db_session, monkeypatch):
    """A Basic user cannot mint a Strategist agent — same 402 the role's own
    routes raise. Nothing is created."""
    _hosted(monkeypatch)
    await _subscribe(client, auth, db_session, "price_basic")

    resp = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "Strategist",
        "configuration": {"avatar": "crew-strategist.webp", "role": "strategist"},
    })
    assert resp.status_code == 402
    assert "Pro plan" in resp.json()["detail"]
    agents = (await client.get("/api/agents", headers=auth)).json()
    assert [a for a in agents if a["display_name"] == "Strategist"] == []

    # an ungated role still works on Basic
    ok = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "Follow-Up",
        "configuration": {"avatar": "crew-followup.webp", "role": "follow_up"},
    })
    assert ok.status_code == 200


async def test_pro_subscriber_can_create_the_gated_role(client, auth, db_session, monkeypatch):
    _hosted(monkeypatch)
    await _subscribe(client, auth, db_session, "price_pro")
    resp = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "Strategist",
        "configuration": {"avatar": "crew-strategist.webp", "role": "strategist"},
    })
    assert resp.status_code == 200, resp.text


async def test_self_host_has_no_role_gating(client, auth):
    """Stripe unconfigured → every role is available, as everywhere else."""
    resp = await client.post("/api/agents", headers=auth, json={
        "adapter_type": "native", "display_name": "Strategist",
        "configuration": {"avatar": "crew-strategist.webp", "role": "strategist"},
    })
    assert resp.status_code == 200, resp.text
