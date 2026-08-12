"""Phase 7 acceptance (§122, §142): strategist uses real state; NO_ACTION valid; Today API."""
from __future__ import annotations

import json

from tests.conftest import setup_mock_provider
from tests.test_integrations import create_demo_connection

PLAN_JSON = json.dumps({
    "summary": "Recover the overdue invoices first.",
    "no_action": False,
    "top_priorities": [
        {"title": "Chase invoice #2041 (€2,400)", "why": "verified overdue", "linked_goal": None},
        {"title": "Reply to ferrytech proposal request", "why": "warm lead", "linked_goal": None},
        {"title": "P3", "why": "", "linked_goal": None},
        {"title": "P4 must be trimmed", "why": "", "linked_goal": None},
    ],
    "background_actions": ["triage inbox"],
    "proposed_experiments": [],
    "risks": ["invoice may be disputed"],
    "confidence": 0.7,
})

NO_ACTION_JSON = json.dumps({
    "summary": "All quiet.", "no_action": True, "top_priorities": [],
    "background_actions": [], "proposed_experiments": [], "risks": [], "confidence": 0.8,
})


async def test_strategist_llm_plan_capped_at_3(client, auth):
    await setup_mock_provider(client, auth, {"open_findings": PLAN_JSON})
    resp = await client.post("/api/strategist/run", headers=auth)
    plan = resp.json()
    assert plan["no_action"] is False
    assert len(plan["top_priorities"]) == 3  # deterministic cap (§58)
    assert plan["source"] == "llm"
    # persisted into markdown report
    doc = (await client.get("/api/documents/by-path",
                            params={"path": "/reports/daily-strategist.md"}, headers=auth)).json()
    assert "2041" in doc["content_md"]


async def test_strategist_no_action(client, auth):
    await setup_mock_provider(client, auth, {"open_findings": NO_ACTION_JSON})
    plan = (await client.post("/api/strategist/run", headers=auth)).json()
    assert plan["no_action"] is True
    assert plan["top_priorities"] == []


async def test_strategist_deterministic_fallback_uses_findings(client, auth):
    """No LLM provider → fallback plan built from real X-Ray findings, not generic advice."""
    await create_demo_connection(client, auth)
    await client.post("/api/xray/run", json={"horizon_days": 90}, headers=auth)
    plan = (await client.post("/api/strategist/run", headers=auth)).json()
    assert plan["source"] == "deterministic_fallback"
    assert plan["no_action"] is False
    titles = " ".join(p["why"] + " " + p["title"] for p in plan["top_priorities"])
    assert "invoice" in titles.lower() or "€" in titles


async def test_today_aggregation(client, auth):
    await create_demo_connection(client, auth)
    await client.post("/api/xray/run", json={"horizon_days": 90}, headers=auth)
    from tests.test_xray import treat_last_run_as_real
    await treat_last_run_as_real(client, auth)
    await setup_mock_provider(client, auth, {"open_findings": PLAN_JSON})
    await client.post("/api/strategist/run", headers=auth)
    today = (await client.get("/api/today", headers=auth)).json()
    assert today["value_found_this_month"]["verified_money_cents"] == 420000
    assert len(today["top_actions"]) == 3
    assert today["market_status"] == "NOT YET SCANNED"
    assert today["needs_you"] == 0
    assert today["handled_automatically"] >= 1
    assert today["goal_trajectory"] == "NO GOALS"


async def test_default_schedules_created(client, auth):
    from sqlalchemy import select

    from backend.core.db import get_sessionmaker
    from backend.core.models import ScheduledJob

    await client.get("/api/today", headers=auth)
    await client.get("/api/today", headers=auth)  # idempotent
    async with get_sessionmaker()() as db:
        jobs = list((await db.execute(select(ScheduledJob))).scalars())
    types = sorted(j.job_type for j in jobs)
    assert types == ["daily_strategist", "market_radar", "weekly_review"]


async def test_scheduler_claim_run_and_idempotency(db_session):
    from datetime import UTC, datetime

    from backend.jobs import scheduler

    ran = []

    @scheduler.handler("test_job")
    async def _test_job(db, job):
        ran.append(job.id)
        return {"ok": True}

    job = await scheduler.enqueue(db_session, "test_job", run_at=datetime.now(UTC),
                                  idempotency_key="t1")
    await db_session.commit()
    dup = await scheduler.enqueue(db_session, "test_job", idempotency_key="t1")
    assert dup is None  # idempotent

    count = await scheduler.tick(db_session, "w1")
    assert count == 1 and ran == [job.id]
    # done job is not re-claimed
    assert await scheduler.tick(db_session, "w1") == 0


async def test_scheduler_retry_and_failure(db_session):
    from backend.jobs import scheduler

    @scheduler.handler("failing_job")
    async def _failing(db, job):
        raise RuntimeError("boom")

    job = await scheduler.enqueue(db_session, "failing_job", max_attempts=2)
    await db_session.commit()
    await scheduler.tick(db_session, "w1")
    from sqlalchemy import select

    from backend.core.models import ScheduledJob

    row = (await db_session.execute(select(ScheduledJob).where(ScheduledJob.id == job.id))).scalar_one()
    assert row.attempts == 1 and row.status == "scheduled" and "boom" in row.last_error
    # force due now and run again → failed
    from datetime import UTC, datetime

    row.next_run_at = datetime.now(UTC)
    await db_session.commit()
    await scheduler.tick(db_session, "w1")
    await db_session.refresh(row)
    assert row.status == "failed"


async def test_interval_job_reschedules(db_session):
    from backend.jobs import scheduler

    @scheduler.handler("interval_job")
    async def _interval(db, job):
        return {"tick": True}

    job = await scheduler.enqueue(db_session, "interval_job", interval_seconds=3600)
    await db_session.commit()
    await scheduler.tick(db_session, "w1")
    from sqlalchemy import select

    from backend.core.models import ScheduledJob

    row = (await db_session.execute(select(ScheduledJob).where(ScheduledJob.id == job.id))).scalar_one()
    assert row.status == "scheduled"
    assert row.payload_json["last_result"] == {"tick": True}
