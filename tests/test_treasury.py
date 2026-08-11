"""Phase 11-12 acceptance (§126, §140): deterministic treasury, approvals, simulator,
agent-scoped limits, telegram approvals. Example policy: monthly €500, daily €100,
auto ≤ €25, approval €25.01–€100, hard max €100."""
from __future__ import annotations

from tests.conftest import auth_headers

EXAMPLE_POLICY = {
    "monthly_limit_cents": 50000,
    "daily_limit_cents": 10000,
    "per_transaction_hard_limit_cents": 10000,
    "autonomous_threshold_cents": 2500,
    "approval_threshold_cents": 10000,
    "spending_enabled": True,
}


async def set_policy(client, auth, **overrides):
    resp = await client.patch("/api/treasury", json={**EXAMPLE_POLICY, **overrides}, headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def spend(client, auth, amount_cents, **kw):
    resp = await client.post("/api/spend-intents", json={
        "amount_cents": amount_cents, "purpose": kw.pop("purpose", "test spend"), **kw,
    }, headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_mandatory_cases(client, auth):
    """§140: €12 auto, €70 approval, €150 deny, OFF+€1 deny."""
    await set_policy(client, auth)
    i12 = await spend(client, auth, 1200)
    assert i12["status"] == "executed"  # auto-approved and simulated-executed

    i70 = await spend(client, auth, 7000)
    assert i70["status"] == "awaiting_approval"
    assert i70["approval_request_id"]

    i150 = await spend(client, auth, 15000)
    assert i150["status"] == "denied"
    assert "hard" in i150["decision_reason"]

    await set_policy(client, auth, spending_enabled=False)
    i1 = await spend(client, auth, 100)
    assert i1["status"] == "denied"
    assert "disabled" in i1["decision_reason"]


async def test_spending_kill_switch(client, auth):
    await set_policy(client, auth)
    await client.post("/api/settings/kill-switch",
                      json={"switch": "disable_spending", "on": True}, headers=auth)
    intent = await spend(client, auth, 1200)
    assert intent["status"] == "denied"
    assert "kill switch" in intent["decision_reason"]


async def test_approval_lifecycle_dashboard(client, auth):
    await set_policy(client, auth)
    await spend(client, auth, 7000, purpose="100 verified sales leads")
    approvals = (await client.get("/api/approvals", headers=auth)).json()
    assert len(approvals) == 1
    resp = await client.post(f"/api/approvals/{approvals[0]['id']}/resolve",
                             json={"approve": True}, headers=auth)
    assert "Approved and executed" in resp.json()["result"]
    intents = (await client.get("/api/spend-intents", headers=auth)).json()
    assert intents[0]["status"] == "executed"
    # double-resolve fails
    resp = await client.post(f"/api/approvals/{approvals[0]['id']}/resolve",
                             json={"approve": False}, headers=auth)
    assert resp.status_code == 400

    # deny path (€28: above autonomous, within remaining daily budget)
    await spend(client, auth, 2800)
    approvals = (await client.get("/api/approvals", headers=auth)).json()
    resp = await client.post(f"/api/approvals/{approvals[0]['id']}/resolve",
                             json={"approve": False}, headers=auth)
    assert "Denied" in resp.json()["result"]
    intents = (await client.get("/api/spend-intents", headers=auth)).json()
    assert intents[0]["status"] == "denied"


async def test_daily_and_monthly_budgets(client, auth):
    await set_policy(client, auth, daily_limit_cents=3000)
    a = await spend(client, auth, 2000)
    assert a["status"] == "executed"
    b = await spend(client, auth, 1500)  # 2000+1500 > 3000
    assert b["status"] == "denied"
    assert "daily" in b["decision_reason"]


async def test_agent_scoped_spending(client, auth):
    """§74: Hermes max €20 autonomous; unbound agent denied."""
    await set_policy(client, auth)
    agents = (await client.get("/api/agents", headers=auth)).json()
    native_id = agents[0]["id"]

    # agent without binding → denied
    intent = await spend(client, auth, 1000, agent_id=native_id)
    assert intent["status"] == "denied"
    assert "agent" in intent["decision_reason"]

    # bind with €20 autonomous cap
    await client.post("/api/treasury/agent-binding", json={
        "agent_id": native_id, "spending_enabled": True, "max_autonomous_cents": 2000,
    }, headers=auth)
    auto = await spend(client, auth, 1500, agent_id=native_id)
    assert auto["status"] == "executed"
    over = await spend(client, auth, 2200, agent_id=native_id)  # over agent cap, under approval
    assert over["status"] == "awaiting_approval"

    # disable agent spending entirely
    await client.post("/api/treasury/agent-binding", json={
        "agent_id": native_id, "spending_enabled": False,
    }, headers=auth)
    off = await spend(client, auth, 500, agent_id=native_id)
    assert off["status"] == "denied"


async def test_vendor_category_rules(client, auth):
    await set_policy(client, auth, blocked_vendors=["scamcorp"], allowed_categories=["lead_data"])
    bad_vendor = await spend(client, auth, 1000, vendor="scamcorp", category="lead_data")
    assert bad_vendor["status"] == "denied"
    bad_cat = await spend(client, auth, 1000, vendor="ok", category="gambling")
    assert bad_cat["status"] == "denied"
    ok = await spend(client, auth, 1000, vendor="ok", category="lead_data")
    assert ok["status"] == "executed"


async def test_simulator(client, auth):
    """§77: simulator exercises the policy without persisting or spending."""
    await set_policy(client, auth)
    resp = await client.post("/api/treasury/simulate", json={"cases": [
        {"amount_cents": 1200}, {"amount_cents": 7000}, {"amount_cents": 15000},
    ]}, headers=auth)
    results = resp.json()["results"]
    assert [r["decision"] for r in results] == ["auto_approve", "require_approval", "deny"]
    # nothing persisted
    assert (await client.get("/api/spend-intents", headers=auth)).json() == []


async def test_ledger_records_spend_events(client, auth):
    await set_policy(client, auth)
    await spend(client, auth, 1200)
    await spend(client, auth, 15000)
    acts = (await client.get("/api/activity", params={"filter": "money"}, headers=auth)).json()
    types = [e["event_type"] for e in acts]
    assert "spend_requested" in types and "spend_approved" in types
    assert "spend_denied" in types and "spend_executed" in types


async def test_telegram_approval_flow(client, auth, db_session):
    """§126: approve via Telegram inline buttons."""
    from backend.api.routes import telegram as tg_routes
    from backend.telegram.gateway import Gateway
    from tests.fake_telegram import FakeTelegramClient, make_text_update

    fake = FakeTelegramClient()
    gateway = Gateway(fake)
    tg_routes.set_gateway(gateway)
    try:
        code = (await client.post("/api/telegram/pairing-code", headers=auth)).json()["code"]
        await gateway.process_update(db_session, make_text_update(555, 555, f"/link {code}"))
        await set_policy(client, auth)
        await spend(client, auth, 7000, purpose="100 verified sales leads")
        # notification with inline buttons was sent
        notif = fake.sent_messages[-1]
        assert "€70.00" in notif["text"]
        buttons = notif["reply_markup"]["inline_keyboard"][0]
        assert buttons[0]["text"] == "APPROVE"
        # simulate pressing APPROVE
        callback = {"callback_query": {
            "id": "cb1", "from": {"id": 555},
            "message": {"chat": {"id": 555}},
            "data": buttons[0]["callback_data"],
        }}
        await gateway.process_update(db_session, callback)
        intents = (await client.get("/api/spend-intents", headers=auth)).json()
        assert intents[0]["status"] == "executed"
    finally:
        tg_routes.set_gateway(None)


async def test_treasury_tenancy(client):
    h_a = await auth_headers(client, "ta@example.com")
    h_b = await auth_headers(client, "tb@example.com")
    await set_policy(client, h_a)
    await spend(client, h_a, 7000)
    approvals_b = (await client.get("/api/approvals", headers=h_b)).json()
    assert approvals_b == []
    approvals_a = (await client.get("/api/approvals", headers=h_a)).json()
    resp = await client.post(f"/api/approvals/{approvals_a[0]['id']}/resolve",
                             json={"approve": True}, headers=h_b)
    assert resp.status_code == 400  # not found for user B


async def test_experiment_lifecycle(client, auth):
    """§125: opportunity → micro test → measured result with criteria + ledger."""

    from tests.conftest import setup_mock_provider
    from tests.test_market import STRONG_SIGNALS

    await setup_mock_provider(client, auth, {"market radar": STRONG_SIGNALS})
    scan = (await client.post("/api/market/scan", headers=auth)).json()
    opp_id = scan["opportunity_id"]

    exp = (await client.post("/api/experiments", json={
        "hypothesis": "Veterinary clinics will pay for AI call handling.",
        "metric": "qualified_replies", "expected_result": ">= 3 qualified replies",
        "deadline": "2026-08-15", "cash_budget_cents": 5000,
        "human_time_budget_minutes": 120,
        "success_criterion": ">= 3 qualified replies",
        "kill_criterion": "< 1 qualified reply",
        "opportunity_id": opp_id, "prediction_probability": 0.6,
    }, headers=auth)).json()
    assert exp["status"] == "draft"

    started = (await client.post(f"/api/experiments/{exp['id']}/start", headers=auth)).json()
    assert started["status"] == "running"
    opp = (await client.get(f"/api/opportunities/{opp_id}", headers=auth)).json()
    assert opp["status"] == "micro_test"

    stopped = (await client.post(f"/api/experiments/{exp['id']}/stop", json={
        "outcome": "succeeded", "observed_value": 4, "notes": "4 qualified replies",
    }, headers=auth)).json()
    assert stopped["status"] == "succeeded"
    opp = (await client.get(f"/api/opportunities/{opp_id}", headers=auth)).json()
    assert opp["status"] == "validated"

    preds = (await client.get("/api/predictions", headers=auth)).json()
    assert preds and preds[0]["status"] == "resolved"

    acts = (await client.get("/api/activity", headers=auth)).json()
    types = [e["event_type"] for e in acts]
    for t in ("experiment_created", "experiment_started", "experiment_stopped", "outcome_recorded"):
        assert t in types, t


async def test_experiment_requires_criteria(client, auth):
    resp = await client.post("/api/experiments", json={
        "hypothesis": "x", "success_criterion": "", "kill_criterion": "",
    }, headers=auth)
    assert resp.status_code == 400
