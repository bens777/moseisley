"""Phase 13 acceptance (§128): prediction 10, observed 2 → meaningful error identified."""
from __future__ import annotations


async def test_auditor_identifies_prediction_error(client, auth):
    p = (await client.post("/api/predictions", json={
        "statement": "10 qualified meetings", "probability": 0.8,
        "metric": "qualified_meetings", "target_value": 10,
    }, headers=auth)).json()
    await client.post("/api/outcomes", json={
        "prediction_id": p["id"], "observed_value": 2, "source": "manual",
    }, headers=auth)

    report = (await client.post("/api/auditor/weekly-review", headers=auth)).json()
    assert report["predictions_reviewed"] == 1
    assert report["calibration"]["meaningful_misses"] == 1
    review = report["reviews"][0]
    assert review["target"] == 10 and review["observed"] == 2
    assert review["meaningful_miss"] is True
    assert review["hit"] is False
    # brier for p=0.8, miss → 0.64
    assert abs(report["calibration"]["brier_score"] - 0.64) < 1e-6

    doc = (await client.get("/api/documents/by-path",
                            params={"path": "/reports/weekly-review.md"}, headers=auth)).json()
    assert "MISS" in doc["content_md"]
    acts = (await client.get("/api/activity", headers=auth)).json()
    assert any(e["event_type"] == "audit_completed" for e in acts)


async def test_auditor_experiment_budget_check(client, auth):
    from tests.test_treasury import set_policy, spend

    await set_policy(client, auth)
    exp = (await client.post("/api/experiments", json={
        "hypothesis": "Paid leads convert", "cash_budget_cents": 1000,
        "success_criterion": ">=1 sale", "kill_criterion": "0 sales after 50 leads",
    }, headers=auth)).json()
    await client.post(f"/api/experiments/{exp['id']}/start", headers=auth)
    # spend €20 against a €10 experiment budget (treasury allows it; auditor flags it)
    await spend(client, auth, 2000, experiment_id=exp["id"])
    await client.post(f"/api/experiments/{exp['id']}/stop",
                      json={"outcome": "killed", "observed_value": 0}, headers=auth)

    report = (await client.post("/api/auditor/weekly-review", headers=auth)).json()
    assert report["experiments"]["experiments_evaluated"] == 1
    assert report["experiments"]["over_budget"] == 1


async def test_auditor_action_truthfulness(client, auth):
    from tests.test_integrations import create_demo_connection

    await create_demo_connection(client, auth)
    await client.post("/api/integrations/invoke", json={
        "capability": "gmail.read", "operation": "gmail.get_all_messages",
    }, headers=auth)
    report = (await client.post("/api/auditor/weekly-review", headers=auth)).json()
    assert report["actions"]["tool_executions"] >= 1
    assert report["actions"]["by_status"].get("SUCCESS", 0) >= 1
