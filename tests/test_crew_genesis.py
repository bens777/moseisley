"""Crew Genesis: AI proposal → review → create, and the re-run/skip paths."""
from __future__ import annotations

import json

from backend.core.config import get_settings
from backend.core.models import AgentConfig, SubscriptionState
from tests.conftest import setup_mock_provider

GOOD_PROPOSAL = json.dumps({"crew": [
    {"role": "follow_up", "name": "Chaser", "avatar": "crew-followup.webp",
     "rationale": "Recovers leads you dropped."},
    {"role": "inbox_triage", "name": "Sorter", "avatar": "crew-bartender.webp",
     "rationale": "Keeps your inbox from eating the day."},
]})


def _hosted(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_genesis")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")


async def _subscribe(client, auth, db_session, price_id: str):
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    db_session.add(SubscriptionState(user_id=uid, status="active", price_id=price_id))
    await db_session.commit()


# ── proposal: validation + fallback ─────────────────────────────────

async def test_proposal_returns_validated_crew(client, auth):
    await setup_mock_provider(client, auth, responses={"assembling": GOOD_PROPOSAL,
                                                       "Moseisley": GOOD_PROPOSAL})
    resp = await client.post("/api/crew/genesis/propose", headers=auth,
                             json={"intent": "Prospect and follow up leads"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "llm"
    assert [m["role"] for m in body["crew"]] == ["follow_up", "inbox_triage"]
    assert body["crew"][0]["avatar"] == "crew-followup.webp"
    assert body["crew"][0]["mission"]           # real ROLES description attached


async def test_invalid_json_falls_back_to_the_template(client, auth):
    """Two unusable answers → deterministic starter crew, never a 500."""
    await setup_mock_provider(client, auth, responses={"Moseisley": "not json at all"})
    resp = await client.post("/api/crew/genesis/propose", headers=auth,
                             json={"intent": "help me"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert [m["role"] for m in body["crew"]] == ["follow_up", "inbox_triage",
                                                 "commitment_tracker"]


async def test_invented_roles_and_avatars_are_dropped_or_snapped(client, auth):
    bad = json.dumps({"crew": [
        {"role": "supreme_leader", "name": "Nope", "avatar": "crew-dev.webp", "rationale": "x"},
        {"role": "radar", "name": "Radar", "avatar": "../etc/passwd", "rationale": "x"},
    ]})
    await setup_mock_provider(client, auth, responses={"Moseisley": bad})
    body = (await client.post("/api/crew/genesis/propose", headers=auth,
                              json={"intent": "watch my market"})).json()
    roles = [m["role"] for m in body["crew"]]
    assert "supreme_leader" not in roles          # invented role dropped
    assert roles == ["radar"]
    assert body["crew"][0]["avatar"] == "crew-radar.webp"   # snapped to a shipped id


# ── gating ──────────────────────────────────────────────────────────

async def test_gated_roles_flagged_for_trial_user(client, auth, monkeypatch):
    proposal = json.dumps({"crew": [
        {"role": "radar", "name": "Radar", "avatar": "crew-radar.webp", "rationale": "x"},
        {"role": "follow_up", "name": "Chaser", "avatar": "crew-followup.webp", "rationale": "y"},
    ]})
    # connect the provider first: on a hosted plan a trial user cannot add keys
    await setup_mock_provider(client, auth, responses={"Moseisley": proposal})
    _hosted(monkeypatch)
    body = (await client.post("/api/crew/genesis/propose", headers=auth,
                              json={"intent": "watch my market"})).json()
    by_role = {m["role"]: m for m in body["crew"]}
    assert by_role["radar"]["gated"] is True      # Pro-only → badge, unselected
    assert by_role["follow_up"]["gated"] is False
    assert "radar" in body["gated_roles"]


async def test_apply_excludes_gated_roles_without_402(client, auth, db_session, monkeypatch):
    """A trial user submitting a gated role gets a clean result, not an error."""
    _hosted(monkeypatch)
    resp = await client.post("/api/crew/genesis/apply", headers=auth, json={"crew": [
        {"role": "radar", "name": "Radar", "avatar": "crew-radar.webp"},
        {"role": "follow_up", "name": "Chaser", "avatar": "crew-followup.webp"},
    ]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["blocked"] == ["radar"]
    assert body["enabled"] == ["follow_up"]
    assert [c["role"] for c in body["created"]] == ["follow_up"]


async def test_pro_user_gets_the_full_crew(client, auth, db_session, monkeypatch):
    _hosted(monkeypatch)
    await _subscribe(client, auth, db_session, "price_pro")
    resp = await client.post("/api/crew/genesis/apply", headers=auth, json={"crew": [
        {"role": "radar", "name": "Radar", "avatar": "crew-radar.webp"},
        {"role": "xray", "name": "X-Ray", "avatar": "crew-xray.webp"},
    ]})
    body = resp.json()
    assert body["blocked"] == []
    assert sorted(body["enabled"]) == ["radar", "xray"]

    agents = (await client.get("/api/agents", headers=auth)).json()
    made = {a["display_name"]: a for a in agents}
    assert made["Radar"]["adapter_type"] == "native"
    assert made["Radar"]["configuration"]["avatar"] == "crew-radar.webp"
    assert made["X-Ray"]["configuration"]["role"] == "xray"


# ── welcome message, re-run, skip ───────────────────────────────────

async def test_apply_posts_a_real_manager_message(client, auth):
    await client.post("/api/crew/genesis/apply", headers=auth, json={"crew": [
        {"role": "follow_up", "name": "Chaser", "avatar": "crew-followup.webp"}]})
    msgs = (await client.get("/api/manager/messages", headers=auth)).json()
    assert msgs, "welcome should live in the Manager history"
    assert "assembled your crew" in msgs[-1]["content"]
    assert "Chaser" in msgs[-1]["content"]


async def test_rerun_is_non_destructive_and_removes_only_unselected(
        client, auth, db_session):
    await client.post("/api/crew/genesis/apply", headers=auth, json={"crew": [
        {"role": "follow_up", "name": "Chaser", "avatar": "crew-followup.webp"},
        {"role": "inbox_triage", "name": "Sorter", "avatar": "crew-bartender.webp"}]})
    first = {a["display_name"]: a["id"] for a in
             (await client.get("/api/agents", headers=auth)).json()}

    # re-run keeping follow_up (same agent must survive) and dropping inbox_triage
    resp = await client.post("/api/crew/genesis/apply", headers=auth, json={
        "crew": [{"role": "follow_up", "name": "Chaser", "avatar": "crew-followup.webp"}],
        "remove_roles": ["inbox_triage"]})
    assert resp.status_code == 200
    assert resp.json()["created"] == []          # nothing duplicated

    agents = {a["display_name"]: a["id"] for a in
              (await client.get("/api/agents", headers=auth)).json()}
    assert agents.get("Chaser") == first["Chaser"]   # same row, untouched
    assert "Sorter" not in agents


async def test_skip_sets_the_flag_and_creates_nothing(client, auth, db_session):
    from sqlalchemy import func, select

    resp = await client.post("/api/crew/genesis/apply", headers=auth, json={"skip": True})
    assert resp.json() == {"skipped": True, "created": [], "enabled": [], "removed": []}

    state = (await client.get("/api/crew/genesis/state", headers=auth)).json()
    assert state["done"] is True and state["skipped"] is True

    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    made = (await db_session.execute(select(func.count()).select_from(AgentConfig).where(
        AgentConfig.user_id == uid, AgentConfig.adapter_type == "native"))).scalar_one()
    assert made <= 1          # only the default native agent from registration


async def test_state_reports_roles_and_existing_agents(client, auth):
    state = (await client.get("/api/crew/genesis/state", headers=auth)).json()
    assert state["done"] is False
    assert any(r["role"] == "radar" for r in state["roles"])
    assert all(r["avatar"].startswith("crew-") for r in state["roles"])
