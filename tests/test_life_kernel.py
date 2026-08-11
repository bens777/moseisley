"""Phase 2 acceptance (§117): goal compiler → structured goal + markdown + ledger + query."""
from __future__ import annotations

import json

from tests.conftest import auth_headers, setup_mock_provider

GOAL_JSON = json.dumps({
    "metric": "monthly_independent_income",
    "title": "€8,000/month independent income",
    "target": 8000,
    "unit": "EUR",
    "currency": "EUR",
    "deadline": "2027-06-01",
    "constraints": {"max_weekly_work_hours": 35},
    "missing_critical": [],
})

INCOMPLETE_GOAL_JSON = json.dumps({
    "metric": "monthly_independent_income",
    "title": "Grow income",
    "target": None,
    "unit": "EUR",
    "currency": "EUR",
    "deadline": None,
    "constraints": {},
    "missing_critical": ["target"],
})


async def test_goal_compiler_full_flow(client, auth):
    await setup_mock_provider(client, auth, {"8,000": GOAL_JSON, "8000": GOAL_JSON})
    resp = await client.post(
        "/api/goals/compile",
        json={"text": "I want €8,000/month independent income by June 1 2027 while working under 35 hours/week."},
        headers=auth,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    goal = body["goal"]
    assert goal["metric"] == "monthly_independent_income"
    assert goal["target_value"] == 8000
    assert goal["deadline"] == "2027-06-01"
    assert goal["constraints"] == {"max_weekly_work_hours": 35}

    # queryable state
    goals = (await client.get("/api/goals", headers=auth)).json()
    assert len(goals) == 1

    # markdown focus updated
    focus = (await client.get("/api/documents/by-path", params={"path": "/context/focus.md"}, headers=auth)).json()
    assert "8000" in focus["content_md"] and "max_weekly_work_hours" in focus["content_md"]

    # ledger event
    activity = (await client.get("/api/activity", headers=auth)).json()
    assert any(e["event_type"] == "goal_created" for e in activity)


async def test_goal_compiler_clarification(client, auth):
    await setup_mock_provider(client, auth, {"grow my income": INCOMPLETE_GOAL_JSON})
    resp = await client.post("/api/goals/compile", json={"text": "I want to grow my income"}, headers=auth)
    body = resp.json()
    assert body["status"] == "needs_clarification"
    assert "target" in body["question"].lower()
    # answer the follow-up, merging prior extraction
    await setup_mock_provider(client, auth, {"5000": json.dumps({"target": 5000, "missing_critical": []})})
    resp2 = await client.post(
        "/api/goals/compile",
        json={"text": "5000 per month", "prior_extracted": body["extracted"]},
        headers=auth,
    )
    body2 = resp2.json()
    assert body2["status"] == "created"
    assert body2["goal"]["target_value"] == 5000


async def test_documents_defaults_and_export_import(client, auth):
    docs = (await client.get("/api/documents", headers=auth)).json()
    paths = {d["path"] for d in docs}
    assert {"/context/constitution.md", "/context/ideal-state.md", "/context/focus.md"} <= paths

    resp = await client.put(
        "/api/documents",
        json={"path": "/projects/saas.md", "content_md": "# SaaS project\nnotes"},
        headers=auth,
    )
    assert resp.status_code == 200
    export = (await client.get("/api/documents/export", headers=auth)).json()
    assert any(d["path"] == "/projects/saas.md" for d in export["documents"])

    # import into a second user (no lock-in)
    h2 = await auth_headers(client, "importer@example.com")
    resp = await client.post("/api/documents/import", json=export, headers=h2)
    assert resp.json()["imported"] >= 4
    doc = (await client.get("/api/documents/by-path", params={"path": "/projects/saas.md"}, headers=h2)).json()
    assert "SaaS" in doc["content_md"]


async def test_constitution_ai_immutable(db_session, client, auth):
    from backend.documents import service as documents

    me = (await client.get("/api/me", headers=auth)).json()
    await documents.get_or_create(db_session, me["id"], documents.CONSTITUTION_PATH)
    import pytest

    with pytest.raises(PermissionError):
        await documents.upsert_document(
            db_session, me["id"], documents.CONSTITUTION_PATH, "# hacked", actor_type="agent"
        )


async def test_chat_context_aware(client, auth):
    await setup_mock_provider(client, auth, {"8000": GOAL_JSON, "what should i focus": "Focus on your income goal."})
    await client.post("/api/goals/compile", json={"text": "income 8000 by june"}, headers=auth)
    resp = await client.post("/api/chat/message", json={"text": "what should I focus on?"}, headers=auth)
    assert resp.status_code == 200
    assert "income goal" in resp.json()["reply"]
    messages = (await client.get("/api/chat/messages", headers=auth)).json()
    assert [m["role"] for m in messages][-2:] == ["user", "assistant"]


async def test_chat_blocked_when_paused(client, auth):
    await setup_mock_provider(client, auth)
    await client.post("/api/settings/kill-switch", json={"switch": "pause_all_agents", "on": True}, headers=auth)
    resp = await client.post("/api/chat/message", json={"text": "hello"}, headers=auth)
    assert resp.status_code == 423


async def test_decisions_predictions_outcomes(client, auth):
    d = (await client.post("/api/decisions", json={
        "reason": "Outbound looks highest-leverage", "alternatives": ["content", "ads"],
        "selected_action": "Run outbound for 2 weeks", "confidence": 0.6,
    }, headers=auth)).json()
    p = (await client.post("/api/predictions", json={
        "decision_id": d["id"], "statement": "10 qualified meetings in 2 weeks",
        "probability": 0.7, "metric": "qualified_meetings", "target_value": 10,
    }, headers=auth)).json()
    o = (await client.post("/api/outcomes", json={
        "prediction_id": p["id"], "observed_value": 2, "source": "manual",
    }, headers=auth)).json()
    assert o["id"]
    preds = (await client.get("/api/predictions", headers=auth)).json()
    assert preds[0]["status"] == "resolved"


async def test_goal_tenancy(client):
    h_a = await auth_headers(client, "ga@example.com")
    h_b = await auth_headers(client, "gb@example.com")
    await setup_mock_provider(client, h_a, {"9000": GOAL_JSON})
    await client.post("/api/goals/compile", json={"text": "income 9000 by june"}, headers=h_a)
    goals_a = (await client.get("/api/goals", headers=h_a)).json()
    goals_b = (await client.get("/api/goals", headers=h_b)).json()
    assert len(goals_a) == 1 and goals_b == []
    resp = await client.patch(f"/api/goals/{goals_a[0]['id']}", json={"progress": 0.5}, headers=h_b)
    assert resp.status_code == 404
