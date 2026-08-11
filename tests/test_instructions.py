"""Third pass: instruction control layer (§15-§17, §47) and market watch (§34-§38, §60, §64)."""
from __future__ import annotations

import json

from sqlalchemy import select

from backend.core.models import ScheduledJob
from tests.conftest import setup_mock_provider

WATCH = {
    "name": "AI agent market watch",
    "kind": "market_watch",
    "config": {
        "topics": ["OpenClaw", "Claude Code", "AI agents"],
        "accounts": ["@example1", "@example2"],
        "lookback_days": 1,
        "instruction": "Report only material changes and explain why they matter.",
    },
    "schedule": {"frequency": "daily", "time": "08:00", "timezone": "Europe/Paris"},
    "delivery": ["telegram"],
    "assigned_role": "radar",
}

MOCK_BRIEF = json.dumps({
    "material_changes": [
        {"title": "OpenClaw 2.0 shipped", "why_it_matters": "new runtime", "evidence": "@example1 post"},
    ],
    "sentiment": "positive",
    "sentiment_basis": "multiple launch posts with strong engagement",
    "narratives": ["agents go mainstream"], "important_posts": ["@example1: launch"],
    "emerging_topics": [], "pain_points": [], "competitor_movement": [],
    "opportunities": ["integration demand"], "threats": [],
})


async def test_instruction_lifecycle_versioning_and_schedule(client, auth, db_session):
    # create → visible as JSON, scheduled job exists
    resp = await client.post("/api/instructions", headers=auth, json=WATCH)
    assert resp.status_code == 200, resp.text
    ins = resp.json()
    assert ins["version"] == 1 and ins["enabled"] is True
    assert ins["config"]["topics"] == ["OpenClaw", "Claude Code", "AI agents"]
    assert ins["next_run_at"] is not None  # scheduler sees it (§60)

    job = (await db_session.execute(select(ScheduledJob).where(
        ScheduledJob.idempotency_key == f"instruction:{ins['id']}"))).scalars().first()
    assert job is not None and job.job_type == "instruction_run"

    # update bumps version and keeps history
    updated = dict(WATCH, schedule={"frequency": "daily", "time": "07:30",
                                    "timezone": "Europe/Paris"})
    resp = await client.put(f"/api/instructions/{ins['id']}", headers=auth, json=updated)
    assert resp.json()["version"] == 2

    detail = (await client.get(f"/api/instructions/{ins['id']}", headers=auth)).json()
    assert [v["version"] for v in detail["versions"]] == [2, 1]

    # rollback re-applies v1 as v3
    resp = await client.post(f"/api/instructions/{ins['id']}/rollback", headers=auth,
                             json={"version": 1})
    assert resp.json()["version"] == 3
    assert resp.json()["schedule"]["time"] == "08:00"

    # disable cancels the scheduled job
    await client.post(f"/api/instructions/{ins['id']}/toggle", headers=auth,
                      json={"enabled": False})
    remaining = (await db_session.execute(select(ScheduledJob).where(
        ScheduledJob.idempotency_key == f"instruction:{ins['id']}",
        ScheduledJob.status == "scheduled"))).scalars().first()
    assert remaining is None

    # duplicate starts disabled
    copy = (await client.post(f"/api/instructions/{ins['id']}/duplicate", headers=auth)).json()
    assert copy["enabled"] is False and copy["name"].endswith("(copy)")

    # export returns the canonical JSON
    resp = await client.get(f"/api/instructions/{ins['id']}/export", headers=auth)
    assert resp.headers["content-disposition"].startswith("attachment")

    # ledger recorded the chain
    acts = (await client.get("/api/activity", headers=auth)).json()
    types = {e["event_type"] for e in acts}
    assert {"instruction_created", "instruction_updated", "instruction_toggled"} <= types


async def test_instruction_validation(client, auth):
    resp = await client.post("/api/instructions", headers=auth, json={
        "name": "bad", "kind": "nonsense"})
    assert resp.status_code == 400
    resp = await client.post("/api/instructions", headers=auth, json={
        "name": "bad", "kind": "custom",
        "schedule": {"frequency": "daily", "time": "25:99"}})
    assert resp.status_code == 400


async def test_market_watch_run_stores_report(client, auth, db_session):
    """§64 with the offline mock provider: run → report with query window,
    sentiment from evidence, sources, crew run + usage attribution."""
    await setup_mock_provider(client, auth, responses={"Radar": MOCK_BRIEF})
    ins = (await client.post("/api/instructions", headers=auth, json=WATCH)).json()

    result = (await client.post(f"/api/instructions/{ins['id']}/run", headers=auth)).json()
    assert result["sentiment"] == "positive"
    assert result["material_changes"] == 1

    reports = (await client.get("/api/market/reports", headers=auth)).json()
    assert len(reports) == 1
    r = reports[0]
    assert r["summary"]["material_changes"][0]["title"] == "OpenClaw 2.0 shipped"
    assert r["query"]["topics"] == WATCH["config"]["topics"]
    assert r["query"]["mock"] is True  # honest about offline mode
    assert r["sentiment"] == "positive"
    assert r["crew_run_id"]

    # instruction records last run
    detail = (await client.get(f"/api/instructions/{ins['id']}", headers=auth)).json()
    assert detail["last_run_at"] is not None
    assert detail["last_result"]["sentiment"] == "positive"

    # crew run + usage attribution (§64 tokens/runtime)
    overview = (await client.get("/api/metrics/overview", headers=auth)).json()
    assert overview["operations_completed"] >= 1
    usage = (await client.get("/api/metrics/usage", headers=auth,
                              params={"window": "today"})).json()
    agents = {r["key"]: r for r in usage["breakdowns"]["agent"]}
    assert agents["radar"]["total_tokens"] > 0


async def test_instructions_tenancy(client, auth):
    from tests.conftest import auth_headers
    ins = (await client.post("/api/instructions", headers=auth, json=WATCH)).json()
    other = await auth_headers(client, "intruder-ins@example.com")
    assert (await client.get(f"/api/instructions/{ins['id']}", headers=other)).status_code == 404
    assert (await client.get("/api/instructions", headers=other)).json() == []
