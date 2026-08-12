"""Phase 5 acceptance (§120, §136): evidence-backed findings, verified/estimated separation,
no fabricated money."""
from __future__ import annotations

from backend.xray.analyzers import parse_amount_cents
from tests.conftest import auth_headers
from tests.test_integrations import create_demo_connection


def test_parse_amount_cents():
    assert parse_amount_cents("invoice for €2,400 overdue") == 240000
    assert parse_amount_cents("€1.800 unpaid") == 180000
    assert parse_amount_cents("$500 payment") == 50000
    assert parse_amount_cents("EUR 1.234,56 due") == 123456
    assert parse_amount_cents("no money here") is None


async def run_xray(client, auth, horizon=90):
    resp = await client.post("/api/xray/run", json={"horizon_days": horizon}, headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()
async def treat_last_run_as_real(client, headers):
    """The suite feeds X-Ray Gmail-shaped fixture data through the retired demo
    adapter. Runs from it are flagged `demo_data`, and the product now hides
    those everywhere — correctly. These tests are about the ENGINE and the API
    shape, so drop the provenance flag and let the run be read as an ordinary
    one. Provenance and exclusion are covered in tests/test_no_fictional_data.py.
    """
    from sqlalchemy import select

    from backend.core.db import get_sessionmaker
    from backend.core.models import XRayRun

    me = (await client.get("/api/me", headers=headers)).json()
    async with get_sessionmaker()() as db:
        run = (await db.execute(
            select(XRayRun).where(XRayRun.user_id == me["id"])
            .order_by(XRayRun.created_at.desc()).limit(1))).scalars().first()
        summary = dict(run.summary_json or {})
        summary.pop("demo_data", None)
        run.summary_json = summary
        await db.commit()


async def test_xray_on_demo_data(client, auth):
    await create_demo_connection(client, auth)
    run = await run_xray(client, auth)
    await treat_last_run_as_real(client, auth)
    assert run["status"] == "completed"
    summary = run["summary"]
    assert summary["emails_analyzed"] > 30
    assert summary["events_analyzed"] > 30
    # demo data contains two overdue invoices: €2,400 + €1,800
    assert summary["verified_money_cents"] == 420000
    assert summary["estimated_time_recoverable_minutes"] > 0

    latest = (await client.get("/api/xray/latest", headers=auth)).json()
    findings = latest["findings"]
    assert latest["no_verified_money_message"] is None

    # verified/estimated separation (§42-43)
    assert all(f["verified"] for f in findings["found_money"])
    assert all(not f["verified"] for f in findings.get("estimated_opportunity", []))
    # every finding carries evidence and confidence
    for group in findings.values():
        for f in group:
            assert f["evidence"], f
            assert 0 <= f["confidence"] <= 1
    # source references on money findings (§136)
    assert findings["found_money"][0]["source_references"]

    # lost commitments detected (proposal promised to ferrytech, never sent)
    assert any("ferrytech" in f["title"] or "ferrytech" in f["description"]
               for f in findings.get("lost_commitment", []))

    # shadow backtest clearly labeled as simulation (§49)
    backtest = findings.get("shadow_backtest", [])
    assert backtest and "RETROSPECTIVE SIMULATION" in backtest[0]["title"]


async def test_xray_no_data_no_invented_money(client, auth):
    """No connected integrations → no findings, and the explicit no-money message (§120)."""
    run = await run_xray(client, auth)
    assert run["status"] == "completed"
    assert run["summary"]["verified_money_cents"] == 0
    latest = (await client.get("/api/xray/latest", headers=auth)).json()
    assert latest["no_verified_money_message"] == "No verified recoverable money found."
    assert latest["findings"].get("found_money") is None


async def test_goal_drift_uses_stated_priority(client, auth):
    """With an income goal, product-heavy demo calendar triggers a drift warning."""
    import json

    from tests.conftest import setup_mock_provider

    goal_json = json.dumps({
        "metric": "monthly_independent_income", "title": "Income goal", "target": 10000,
        "unit": "EUR", "currency": "EUR", "deadline": None, "constraints": {}, "missing_critical": [],
    })
    await setup_mock_provider(client, auth, {"10000": goal_json})
    await client.post("/api/goals/compile", json={"text": "income 10000 monthly"}, headers=auth)
    await create_demo_connection(client, auth)
    await run_xray(client, auth)
    await treat_last_run_as_real(client, auth)
    latest = (await client.get("/api/xray/latest", headers=auth)).json()
    drift = latest["findings"]["goal_drift"][0]
    assert "sales" in drift["title"] or "sales" in drift["description"]
    assert "approximate" in drift["description"]


async def test_finding_status_update_and_tenancy(client, auth):
    await create_demo_connection(client, auth)
    await run_xray(client, auth)
    await treat_last_run_as_real(client, auth)
    latest = (await client.get("/api/xray/latest", headers=auth)).json()
    finding = latest["findings"]["found_money"][0]
    resp = await client.patch(f"/api/xray/findings/{finding['id']}",
                              json={"status": "dismissed"}, headers=auth)
    assert resp.json()["status"] == "dismissed"
    # another tenant can't see or modify
    h_b = await auth_headers(client, "xrayb@example.com")
    assert (await client.get("/api/xray/latest", headers=h_b)).json()["run"] is None
    resp = await client.patch(f"/api/xray/findings/{finding['id']}",
                              json={"status": "actioned"}, headers=h_b)
    assert resp.status_code == 404


# ── demo data provenance + clearing (dashboard "DEMO DATA" banner) ──

async def test_xray_run_records_demo_provenance(client, auth):
    """Runs built from the retired demo dataset say so — that flag is what the
    product now uses to exclude them from every user-facing surface."""
    await create_demo_connection(client, auth)
    run = (await client.post("/api/xray/run", json={"horizon_days": 90},
                             headers=auth)).json()
    assert run["summary"]["demo_data"] is True
    # and because of that flag, the run is invisible to the user
    assert (await client.get("/api/xray/latest", headers=auth)).json()["run"] is None


async def test_clear_demo_removes_connection_and_its_findings(client, auth):
    await create_demo_connection(client, auth)
    await client.post("/api/xray/run", json={"horizon_days": 90}, headers=auth)

    resp = await client.post("/api/integrations/demo/clear", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["runs_removed"] == 1 and body["findings_removed"] > 0

    # the demo connection is gone…
    conns = (await client.get("/api/integrations", headers=auth)).json()
    assert [c for c in conns if c["integration_type"] == "demo"] == []
    # …and so are the sample findings
    after = (await client.get("/api/xray/latest", headers=auth)).json()
    assert after["run"] is None and after["findings"] == {}
    # the purge is on the ledger
    events = (await client.get("/api/activity", headers=auth)).json()
    purges = [e for e in events if e["event_type"] == "data_purged"]
    assert purges and purges[0]["payload"]["scope"] == "demo"


async def test_clear_demo_keeps_real_findings(client, auth, db_session):
    """A user with real data alongside the demo set keeps every real finding."""
    from backend.core.models import XRayFinding, XRayRun

    await create_demo_connection(client, auth)
    await client.post("/api/xray/run", json={"horizon_days": 90}, headers=auth)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]

    real = XRayRun(user_id=uid, horizon_days=90, status="completed",
                   summary_json={"findings": 1, "demo_data": False})
    db_session.add(real)
    await db_session.flush()
    db_session.add(XRayFinding(user_id=uid, run_id=real.id, type="found_money",
                               title="Real unpaid invoice", confidence=0.9, verified=True))
    await db_session.commit()

    resp = await client.post("/api/integrations/demo/clear", headers=auth, json={})
    assert resp.status_code == 200 and resp.json()["runs_removed"] == 1

    latest = (await client.get("/api/xray/latest", headers=auth)).json()
    titles = [f["title"] for group in latest["findings"].values() for f in group]
    assert titles == ["Real unpaid invoice"]


async def test_clear_demo_404_without_demo_data(client, auth):
    resp = await client.post("/api/integrations/demo/clear", headers=auth, json={})
    assert resp.status_code == 404
