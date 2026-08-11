"""Phase 6 acceptance (§121): loops produce draft/proposed actions through policy."""
from __future__ import annotations

from tests.test_integrations import create_demo_connection


async def run(client, auth, loop):
    resp = await client.post(f"/api/autopilot/{loop}/run", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_lost_lead_recovery_creates_drafts(client, auth):
    await create_demo_connection(client, auth)
    result = await run(client, auth, "lost_lead_recovery")
    assert result["status"] == "completed"
    assert result["drafts"], "expected follow-up drafts"
    # demo adapter is read-only → gmail draft attempt falls back to internal draft
    assert all(d["delivered_as"] == "internal_draft" for d in result["drafts"])
    # drafts stored as markdown documents
    docs = (await client.get("/api/documents", params={"prefix": "/drafts/"}, headers=auth)).json()
    assert len(docs) >= 1
    acts = (await client.get("/api/activity", headers=auth)).json()
    assert any(e["event_type"] == "autopilot_draft_created" for e in acts)


async def test_inbox_triage_report(client, auth):
    await create_demo_connection(client, auth)
    result = await run(client, auth, "inbox_triage")
    assert result["status"] == "completed"
    buckets = {i["bucket"]: i["count"] for i in result["items"]}
    assert buckets.get("NOISE", 0) > 0
    assert buckets.get("ACTION REQUIRED", 0) > 0
    doc = (await client.get("/api/documents/by-path",
                            params={"path": "/reports/inbox-triage.md"}, headers=auth)).json()
    assert "Inbox Triage" in doc["content_md"]


async def test_commitment_tracker(client, auth):
    await create_demo_connection(client, auth)
    result = await run(client, auth, "commitment_tracker")
    assert result["status"] == "completed"
    assert result["items"]


async def test_loops_no_action_without_data(client, auth):
    for loop in ("lost_lead_recovery", "follow_up", "commitment_tracker", "inbox_triage", "goal_drift"):
        result = await run(client, auth, loop)
        assert result["status"] == "no_action", loop


async def test_loops_blocked_when_paused(client, auth):
    await create_demo_connection(client, auth)
    await client.post("/api/settings/kill-switch",
                      json={"switch": "pause_all_agents", "on": True}, headers=auth)
    resp = await client.post("/api/autopilot/inbox_triage/run", headers=auth)
    assert resp.status_code == 423
