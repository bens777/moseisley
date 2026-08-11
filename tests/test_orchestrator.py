"""Owner directive acceptance §76: the end-to-end Personal OS scenario, plus
orchestrator tool-loop mechanics, memory provenance, workspace export."""
from __future__ import annotations

import json

import pytest

from backend.core import killswitch
from backend.telegram.gateway import Gateway
from tests.conftest import setup_mock_provider
from tests.fake_telegram import FakeTelegramClient, make_text_update

GOAL_JSON = json.dumps({
    "metric": "monthly_independent_income", "title": "€5,000/month independent income",
    "target": 5000, "unit": "EUR", "currency": "EUR", "deadline": "2026-10-01",
    "constraints": {}, "missing_critical": [],
})

ORCH_SCRIPT = {
    # memory storage flow
    "remember that i don't want to work more than":
        '{"action":"tool","tool":"memory.upsert","args":{"memory_type":"preference",'
        '"key":"max_weekly_work_hours","value":"30"}}',
    "tool result for memory.upsert":
        '{"action":"reply","text":"Noted — I will keep you under 30 hours per week."}',
    # memory recall flow (also used from Telegram — same canonical brain)
    "how many hours per week":
        '{"action":"tool","tool":"memory.read","args":{}}',
    "tool result for memory.read":
        '{"action":"reply","text":"You want to work at most 30 hours per week."}',
    # crew delegation flow
    "ask the challenger":
        '{"action":"tool","tool":"crew.delegate","args":{"role":"challenger",'
        '"task":"review the current strategy"}}',
    "tool result for crew.delegate":
        '{"action":"reply","text":"Challenger verdict: hold — insufficient evidence to pivot."}',
    # challenger's own LLM call (purpose audit)
    "prove the current strategy is wrong": json.dumps({
        "verdict": "hold", "arguments": ["No new evidence."], "missing_data": [],
        "proposed_micro_tests": [], "confidence": 0.5}),
    # goal compilation (fast path)
    "5,000": GOAL_JSON, "5000": GOAL_JSON,
}


async def setup_orchestrator(client, auth):
    await setup_mock_provider(client, auth, ORCH_SCRIPT)
    resp = await client.put("/api/orchestrator",
                            json={"provider": "mock", "model": "mock-1"}, headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_e2e_personal_os_scenario(client, auth, db_session):
    """§76 steps 1-24 (mock provider; live providers are externally blocked)."""
    # 2-3: connect provider, select orchestrator (model must exist in catalog)
    cfg = await setup_orchestrator(client, auth)
    assert cfg["configured"] is True

    # 4-10: goal via chat → compiled → persisted → API → workspace → ledger
    resp = await client.post("/api/chat/message", json={
        "text": "My goal is to reach €5,000/month in independent income by October.",
    }, headers=auth)
    assert "Goal locked in" in resp.json()["reply"]
    goals = (await client.get("/api/goals", headers=auth)).json()
    assert goals[0]["target_value"] == 5000
    ws = (await client.get("/api/workspace/export", headers=auth)).json()["workspace"]
    assert ws["/goals/goals.json"][0]["target"] == 5000
    acts = (await client.get("/api/activity", headers=auth)).json()
    assert any(e["event_type"] == "goal_created" for e in acts)

    # 11-14: explicit memory → validated → persisted → workspace
    resp = await client.post("/api/chat/message", json={
        "text": "Remember that I don't want to work more than 30 hours per week.",
    }, headers=auth)
    assert "30 hours" in resp.json()["reply"]
    memories = (await client.get("/api/memory", headers=auth)).json()
    assert any(m["key"] == "max_weekly_work_hours" and m["value"] == "30" for m in memories)
    assert all(m["provenance"] == "USER_EXPLICIT" for m in memories)
    ws = (await client.get("/api/workspace/export", headers=auth)).json()["workspace"]
    assert any(m["key"] == "max_weekly_work_hours" for m in ws["/memory/preferences.json"])

    # 15-16: Telegram asks — SAME canonical memory answers
    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    code = (await client.post("/api/telegram/pairing-code", headers=auth)).json()["code"]
    await gateway.process_update(db_session, make_text_update(777, 777, f"/link {code}"))
    await gateway.process_update(db_session, make_text_update(
        777, 777, "How many hours per week do I want to work maximum?"))
    assert "30 hours" in fake.sent_messages[-1]["text"]

    # 17-21: delegation → bounded crew job → result + usage metadata + crew run visible
    resp = await client.post("/api/chat/message", json={
        "text": "Ask the challenger to review this strategy.",
    }, headers=auth)
    assert "hold" in resp.json()["reply"].lower()
    crew_state = (await client.get("/api/crew", headers=auth)).json()
    challenger = next(c for c in crew_state["crew"] if c["role"] == "challenger")
    assert challenger["last_run"] is not None
    assert challenger["last_run"]["status"] == "completed"
    events = (await client.get("/api/usage/events", headers=auth)).json()
    assert any(e["crew_role"] == "challenger" for e in events)
    assert any(e["crew_role"] == "orchestrator" for e in events)
    assert all(e["actual_model"] == "mock-1" for e in events if e["status"] == "success")

    # 22-24: EMERGENCY STOP blocks new LLM calls and spending before execution
    resp = await client.post("/api/settings/emergency-stop", json={"on": True}, headers=auth)
    assert resp.json()["emergency_stop"] is True
    me = (await client.get("/api/me", headers=auth)).json()
    from backend.providers import registry

    with pytest.raises(killswitch.KillSwitchEngaged):
        await registry.generate(db_session, me["id"], [{"role": "user", "content": "hi"}])
    intent = (await client.post("/api/spend-intents", json={
        "amount_cents": 100, "purpose": "blocked by emergency stop",
    }, headers=auth)).json()
    assert intent["status"] == "denied"
    assert "EMERGENCY" in intent["decision_reason"]
    acts = (await client.get("/api/activity", headers=auth)).json()
    assert any(e["event_type"] == "system_emergency_stopped" for e in acts)

    # resume restores operation
    await client.post("/api/settings/emergency-stop", json={"on": False}, headers=auth)
    result = await registry.generate(db_session, me["id"],
                                     [{"role": "user", "content": "5000 target"}])
    assert result.model == "mock-1"


async def test_orchestrator_requires_connected_provider(client, auth):
    resp = await client.put("/api/orchestrator",
                            json={"provider": "anthropic", "model": "claude-sonnet-5"},
                            headers=auth)
    assert resp.status_code == 400  # no key connected yet


async def test_orchestrator_rejects_invented_model(client, auth):
    await setup_mock_provider(client, auth)
    resp = await client.put("/api/orchestrator",
                            json={"provider": "mock", "model": "made-up-model-9000"},
                            headers=auth)
    assert resp.status_code == 400


async def test_memory_provenance_ai_inference_never_fact(db_session, client, auth):
    """§25: non-user provenance writing a 'fact' is downgraded to belief."""
    from backend.life_kernel import memory

    me = (await client.get("/api/me", headers=auth)).json()
    row = await memory.upsert(db_session, me["id"], memory_type="fact", key="inferred_thing",
                              value="probably true", provenance="CREW_ANALYSIS")
    assert row.memory_type == "belief"
    row2 = await memory.upsert(db_session, me["id"], memory_type="fact", key="stated_thing",
                               value="true", provenance="USER_EXPLICIT")
    assert row2.memory_type == "fact"


async def test_memory_api_and_tenancy(client, auth):
    from tests.conftest import auth_headers

    await client.post("/api/memory", json={
        "memory_type": "preference", "key": "meeting_earliest_time", "value": "10:00",
    }, headers=auth)
    mine = (await client.get("/api/memory", headers=auth)).json()
    assert mine[0]["key"] == "meeting_earliest_time"
    h_b = await auth_headers(client, "memb@example.com")
    assert (await client.get("/api/memory", headers=h_b)).json() == []
    # search
    hits = (await client.get("/api/memory", params={"q": "10:00"}, headers=auth)).json()
    assert len(hits) == 1


async def test_crew_prompt_override_and_reset(client, auth):
    default = (await client.get("/api/crew/strategist/prompt", headers=auth)).json()
    assert default["uses_default"] is True
    assert "Strategist" in default["prompt"]
    resp = await client.put("/api/crew/strategist/prompt",
                            json={"prompt": "CUSTOM STRATEGIST PROMPT vX"}, headers=auth)
    assert resp.json()["uses_default"] is False
    now = (await client.get("/api/crew/strategist/prompt", headers=auth)).json()
    assert now["prompt"] == "CUSTOM STRATEGIST PROMPT vX"
    # reset
    await client.put("/api/crew/strategist/prompt", json={"prompt": None}, headers=auth)
    back = (await client.get("/api/crew/strategist/prompt", headers=auth)).json()
    assert back["uses_default"] is True
    acts = (await client.get("/api/activity", headers=auth)).json()
    assert sum(1 for e in acts if e["event_type"] == "prompt_changed") == 2


async def test_crew_model_policy_inherit_and_custom(client, auth):
    await setup_mock_provider(client, auth)
    await client.put("/api/orchestrator", json={"provider": "mock", "model": "mock-1"},
                     headers=auth)
    crew_state = (await client.get("/api/crew", headers=auth)).json()
    strategist = next(c for c in crew_state["crew"] if c["role"] == "strategist")
    assert strategist["model_policy"] == "inherit"
    assert strategist["provider"] == "mock"  # inherited from orchestrator

    resp = await client.put("/api/crew/radar/model-policy", json={
        "model_policy": "custom", "provider": "mock", "model": "mock-1",
    }, headers=auth)
    assert resp.status_code == 200
    crew_state = (await client.get("/api/crew", headers=auth)).json()
    radar = next(c for c in crew_state["crew"] if c["role"] == "radar")
    assert radar["model_policy"] == "custom"
    # switching orchestrator must NOT overwrite the explicit custom config
    await client.put("/api/orchestrator", json={"provider": "mock", "model": "mock-1"},
                     headers=auth)
    radar = next(c for c in (await client.get("/api/crew", headers=auth)).json()["crew"]
                 if c["role"] == "radar")
    assert radar["model_policy"] == "custom"


async def test_usage_summary_labeled_sources(client, auth):
    await setup_orchestrator(client, auth)
    await client.post("/api/chat/message",
                      json={"text": "Remember that I don't want to work more than 30 hours"},
                      headers=auth)
    summary = (await client.get("/api/usage/summary", headers=auth)).json()
    assert summary["month"]["requests"] >= 2
    assert summary["month"]["total_tokens"] > 0
    # mock has a $0 pricing snapshot → costs are ESTIMATED at 0.0, never invented
    assert summary["month"]["reported_cost"] == 0.0
    assert "orchestrator" in summary["by_role"]
