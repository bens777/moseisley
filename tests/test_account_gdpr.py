"""GDPR account export & erasure (owner directive §35)."""
from __future__ import annotations

import json

from sqlalchemy import func, select

from backend.core.models import Event, Goal, User
from tests.conftest import TEST_PASSWORD, auth_headers, setup_mock_provider


def _goal_json(target: int) -> str:
    return json.dumps({
        "metric": "monthly_independent_income", "title": "Income", "target": target,
        "unit": "EUR", "currency": "EUR", "deadline": None, "constraints": {},
        "missing_critical": [],
    })


async def _seed(client, headers, target: int) -> None:
    """Create tenant rows across several tables (provider + goal + ledger events)."""
    await setup_mock_provider(client, headers, {str(target): _goal_json(target)})
    await client.post("/api/goals/compile",
                      json={"text": f"income {target} monthly"}, headers=headers)


async def test_account_export_contains_user_data(client, auth):
    await _seed(client, auth, 5000)
    resp = await client.get("/api/account/export", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["account"]["email"] == "founder@example.com"
    assert "events" in body["tables"]  # the ledger is part of the export
    assert "goals" in body["tables"]
    # exported credentials must be ciphertext-free
    assert "encrypted_secret" not in json.dumps(body["tables"].get("provider_connections", []))


async def test_delete_requires_correct_password(client, auth):
    resp = await client.post("/api/account/delete", headers=auth,
                             json={"password": "wrong", "confirm": "DELETE"})
    assert resp.status_code == 403


async def test_delete_requires_confirmation_phrase(client, auth):
    resp = await client.post("/api/account/delete", headers=auth,
                             json={"password": TEST_PASSWORD, "confirm": "yes"})
    assert resp.status_code == 400


async def test_delete_erases_all_tenant_data_including_ledger(client, auth, db_session):
    # create data across several tenant tables
    await _seed(client, auth, 9000)
    me = (await client.get("/api/me", headers=auth)).json()
    uid = me["id"]
    assert (await db_session.execute(
        select(func.count(Event.id)).where(Event.user_id == uid))).scalar_one() > 0

    resp = await client.post("/api/account/delete", headers=auth,
                             json={"password": TEST_PASSWORD, "confirm": "DELETE"})
    assert resp.status_code == 200, resp.text

    # every trace is gone: user row, goals, and the append-only ledger
    for model in (User, Goal, Event):
        col = User.id if model is User else model.user_id
        remaining = (await db_session.execute(
            select(func.count()).select_from(model).where(col == uid))).scalar_one()
        assert remaining == 0, f"{model.__name__} rows survived erasure"


async def test_erased_user_cannot_login(client, auth):
    await client.post("/api/account/delete", headers=auth,
                      json={"password": TEST_PASSWORD, "confirm": "DELETE"})
    resp = await client.post("/api/auth/login",
                             data={"username": "founder@example.com", "password": TEST_PASSWORD})
    assert resp.status_code == 400  # credentials no longer exist


async def test_other_tenant_data_survives_erasure(client, auth, db_session):
    # a second user's data must be untouched by the first user's deletion
    other = await auth_headers(client, email="second@example.com")
    await _seed(client, other, 3000)
    other_id = (await client.get("/api/me", headers=other)).json()["id"]

    await client.post("/api/account/delete", headers=auth,
                      json={"password": TEST_PASSWORD, "confirm": "DELETE"})

    surviving = (await db_session.execute(
        select(func.count(Goal.id)).where(Goal.user_id == other_id))).scalar_one()
    assert surviving >= 1
    # and the second user can still authenticate
    resp = await client.post("/api/auth/login",
                             data={"username": "second@example.com", "password": TEST_PASSWORD})
    assert resp.status_code == 200
