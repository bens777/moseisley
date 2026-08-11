"""Third pass: canonical revenue truth rules, runtime aggregation, KPI overview,
projects/portfolio (§2-§10, §51, §61, §63)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.core.models import CrewRun, LlmUsage
from backend.ops import revenue as revenue_svc


async def _mkproject(client, auth, name="Example SaaS") -> str:
    resp = await client.post("/api/projects", headers=auth, json={
        "name": name, "description": "AI-operated demo",
        "urls": {"website": "https://example.com", "repository": "https://git.example.com/x"},
        "capital_allocated_cents": 20000,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def test_revenue_truth_rules(client, auth, db_session):
    me = (await client.get("/api/me", headers=auth)).json()
    pid = await _mkproject(client, auth)

    # negative / zero amounts refused — refunds are reversals, not negatives
    with pytest.raises(ValueError):
        await revenue_svc.record_event(db_session, me["id"], source="manual", amount_cents=0)
    # unknown source refused
    with pytest.raises(ValueError):
        await revenue_svc.record_event(db_session, me["id"], source="pipeline", amount_cents=100)
    # recurring requires interval
    with pytest.raises(ValueError):
        await revenue_svc.record_event(db_session, me["id"], source="manual",
                                       amount_cents=100, recurring=True)

    # manual API entry works and is labeled manual
    resp = await client.post(f"/api/projects/{pid}/revenue", headers=auth, json={
        "amount_cents": 3400, "currency": "EUR", "description": "Consulting invoice paid",
    })
    assert resp.json()["source"] == "manual"
    # API refuses to impersonate a connected source
    resp = await client.post(f"/api/projects/{pid}/revenue", headers=auth, json={
        "amount_cents": 500, "source": "stripe",
    })
    assert resp.status_code == 400


async def test_verified_mrr_methodology(client, auth, db_session):
    """MRR counts each recurring monthly source once (latest charge within 35d);
    lapsed and reversed sources drop out; yearly is never normalized in."""
    me = (await client.get("/api/me", headers=auth)).json()
    pid = await _mkproject(client, auth)
    uid = me["id"]
    now = datetime.now(UTC)

    # active monthly subscription: two charges, count latest once → 2000
    for days_ago in (33, 3):
        await revenue_svc.record_event(
            db_session, uid, source="manual", amount_cents=2000, currency="EUR",
            project_id=pid, source_ref="sub_A", recurring=True,
            recurrence_interval="monthly", occurred_at=now - timedelta(days=days_ago))
    # lapsed subscription (last charge 60d ago) → excluded
    await revenue_svc.record_event(
        db_session, uid, source="manual", amount_cents=900, currency="EUR",
        project_id=pid, source_ref="sub_lapsed", recurring=True,
        recurrence_interval="monthly", occurred_at=now - timedelta(days=60))
    # yearly plan → never in MRR (count less, not more)
    await revenue_svc.record_event(
        db_session, uid, source="manual", amount_cents=12000, currency="EUR",
        project_id=pid, source_ref="sub_yearly", recurring=True,
        recurrence_interval="yearly", occurred_at=now - timedelta(days=2))
    # one-off revenue → revenue, not MRR
    await revenue_svc.record_event(
        db_session, uid, source="manual", amount_cents=3400, currency="EUR",
        project_id=pid, occurred_at=now - timedelta(days=1))
    # different currency stays separate
    await revenue_svc.record_event(
        db_session, uid, source="manual", amount_cents=1000, currency="USD",
        project_id=pid, source_ref="sub_usd", recurring=True,
        recurrence_interval="monthly", occurred_at=now - timedelta(days=1))
    await db_session.commit()

    mrr = await revenue_svc.verified_mrr(db_session, uid)
    assert mrr == {"EUR": 2000, "USD": 1000}
    rev = await revenue_svc.verified_revenue(db_session, uid, days=30)
    # only charges within the 30d window count (the 33d-old sub_A charge is out)
    assert rev["EUR"] == 2000 + 12000 + 3400
    assert rev["USD"] == 1000

    # reversal removes it from every aggregate
    events = await revenue_svc.list_events(db_session, uid, project_id=pid)
    sub_a_latest = next(e for e in events if e.source_ref == "sub_A")
    await revenue_svc.reverse_event(db_session, uid, sub_a_latest.id)
    await db_session.commit()
    mrr = await revenue_svc.verified_mrr(db_session, uid)
    # older sub_A charge (33d ago) is still within 35d window → still counted
    assert mrr["EUR"] == 2000


async def test_runtime_is_real_not_time_saved(client, auth, db_session):
    """§63: store actual durations; concurrent runs sum past wall-clock."""
    me = (await client.get("/api/me", headers=auth)).json()
    t0 = datetime.now(UTC) - timedelta(minutes=10)
    # two overlapping runs of 5 minutes each → 600s total runtime
    for role in ("strategist", "radar"):
        db_session.add(CrewRun(user_id=me["id"], crew_role=role, status="completed",
                               started_at=t0, finished_at=t0 + timedelta(minutes=5)))
    await db_session.commit()

    overview = (await client.get("/api/metrics/overview", headers=auth)).json()
    assert overview["runtime_week"]["total_seconds"] == 600.0
    assert overview["runtime_week"]["by_role"]["strategist"] == 300.0
    assert "time saved" not in str(overview["methodology"]["runtime"]).lower()


async def test_overview_zero_state_and_kpis(client, auth, db_session):
    """Fresh account: truthful zeros. After real records: real numbers (§51, §61)."""
    me = (await client.get("/api/me", headers=auth)).json()
    o = (await client.get("/api/metrics/overview", headers=auth)).json()
    assert o["operations_completed"] == 0
    assert o["verified_revenue_month"] == {}
    assert o["verified_mrr"] == {}
    assert o["capital_deployed_cents"] == 0
    assert o["usage_week"]["tokens"]["total"] == 0

    pid = await _mkproject(client, auth)
    now = datetime.now(UTC)
    await revenue_svc.record_event(db_session, me["id"], source="manual",
                                   amount_cents=2000, currency="EUR", project_id=pid,
                                   source_ref="sub_B", recurring=True,
                                   recurrence_interval="monthly", occurred_at=now)
    db_session.add(LlmUsage(user_id=me["id"], provider="mock", model="mock-1",
                            purpose="chat", crew_role="radar", project_id=pid,
                            input_tokens=1000, output_tokens=500, total_tokens=1500,
                            estimated_cost=0.02, cost_source="ESTIMATED", status="completed"))
    await db_session.commit()

    o = (await client.get("/api/metrics/overview", headers=auth)).json()
    assert o["verified_mrr"] == {"EUR": 2000}
    assert o["active_projects"] == 1
    assert o["usage_week"]["tokens"]["total"] == 1500
    assert o["usage_week"]["estimated_cost"] == 0.02

    # portfolio exposes the same canonical numbers per project
    portfolio = (await client.get("/api/projects", headers=auth)).json()
    assert portfolio[0]["metrics"]["verified_mrr"] == {"EUR": 2000}
    assert portfolio[0]["metrics"]["ai_tokens_total"] == 1500
    assert portfolio[0]["urls"]["website"] == "https://example.com"

    # usage view breakdowns come from the same records
    usage = (await client.get("/api/metrics/usage", headers=auth, params={"window": "week"})).json()
    agents = {r["key"]: r for r in usage["breakdowns"]["agent"]}
    assert agents["radar"]["total_tokens"] == 1500
    projects = {r["key"]: r for r in usage["breakdowns"]["project"]}
    assert projects[pid]["total_tokens"] == 1500
    assert "provider accounts" in usage["byok_note"]


async def test_projects_tenancy(client, auth):
    from tests.conftest import auth_headers
    pid = await _mkproject(client, auth)
    other = await auth_headers(client, "intruder-projects@example.com")
    resp = await client.get(f"/api/projects/{pid}", headers=other)
    assert resp.status_code == 404
    assert (await client.get("/api/projects", headers=other)).json() == []
