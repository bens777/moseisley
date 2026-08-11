"""Stripe Customer Portal — the only route to cancel, change plan or update a card.

Also pins the webhook behaviour the portal depends on: whatever the user does
inside Stripe comes back as customer.subscription.updated / .deleted and is
synced authoritatively into local entitlement state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import select

from backend.billing import stripe_billing
from backend.core.config import get_settings
from backend.core.models import SubscriptionState

WEBHOOK_SECRET = "whsec_portal_test"


def _configure_stripe(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_portal")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")
    monkeypatch.setattr(s, "stripe_webhook_secret", WEBHOOK_SECRET)


def _mock_stripe(monkeypatch, capture: list | None = None):
    async def fake_post(path: str, data: dict) -> dict:
        if capture is not None:
            capture.append((path, data))
        if path == "/customers":
            return {"id": "cus_portal"}
        if path == "/billing_portal/sessions":
            return {"url": "https://billing.stripe.com/session/test"}
        return {"id": "cs_test", "url": "https://checkout.stripe.test/x"}

    monkeypatch.setattr(stripe_billing, "_stripe_post", fake_post)


async def _subscribe(client, auth, db_session, price_id: str = "price_pro"):
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    row = (await db_session.execute(select(SubscriptionState).where(
        SubscriptionState.user_id == uid))).scalar_one_or_none()
    if row is None:
        row = SubscriptionState(user_id=uid)
        db_session.add(row)
    row.status = "active"
    row.price_id = price_id
    row.stripe_customer_id = "cus_portal"
    await db_session.commit()
    return uid


def _subscription_event(etype: str, user_id: str, *, status: str = "active",
                        price_id: str = "price_pro") -> dict:
    return {"type": etype, "data": {"object": {
        "id": "sub_portal", "customer": "cus_portal", "status": status,
        "cancel_at_period_end": False,
        "current_period_end": int(time.time()) + 30 * 86400,
        "items": {"data": [{"price": {"id": price_id}}]},
        "metadata": {"moseisley_user_id": user_id},
    }}}


async def _post_webhook(client, event: dict):
    payload = json.dumps(event).encode()
    ts = int(time.time())
    mac = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return await client.post("/api/billing/webhook", content=payload,
                             headers={"Stripe-Signature": f"t={ts},v1={mac}",
                                      "Content-Type": "application/json"})


# ── portal endpoint ─────────────────────────────────────────────────

async def test_portal_400_for_user_without_billing_history(client, auth, monkeypatch):
    """Never subscribed → no Stripe customer → nothing to manage."""
    _configure_stripe(monkeypatch)
    _mock_stripe(monkeypatch)
    resp = await client.post("/api/billing/portal", headers=auth, json={})
    assert resp.status_code == 400
    assert "no subscription to manage" in resp.json()["detail"].lower()


async def test_portal_returns_url_for_active_subscriber(client, auth, db_session,
                                                        monkeypatch):
    _configure_stripe(monkeypatch)
    calls: list = []
    _mock_stripe(monkeypatch, calls)
    await _subscribe(client, auth, db_session)

    resp = await client.post("/api/billing/portal", headers=auth, json={})
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://billing.stripe.com/")
    path, data = next(c for c in calls if c[0] == "/billing_portal/sessions")
    assert data["customer"] == "cus_portal"
    assert data["return_url"] == stripe_billing.portal_return_url()


async def test_portal_return_url_prefers_the_env_var(monkeypatch):
    monkeypatch.setattr(get_settings(), "frontend_origin", "https://fallback.example/")
    portal = stripe_billing.portal_settings()
    monkeypatch.setattr(portal, "stripe_portal_return_url", None)
    assert stripe_billing.portal_return_url() == "https://fallback.example/settings"
    monkeypatch.setattr(portal, "stripe_portal_return_url", "https://moseisley.sh/settings")
    assert stripe_billing.portal_return_url() == "https://moseisley.sh/settings"


async def test_portal_424_when_stripe_not_configured(client, auth):
    """Self-host deployment: truthfully unavailable, never faked."""
    resp = await client.post("/api/billing/portal", headers=auth, json={})
    assert resp.status_code == 424


# ── what the portal sends back ──────────────────────────────────────

async def test_cancellation_webhook_downgrades_to_community(client, auth, db_session,
                                                            monkeypatch):
    _configure_stripe(monkeypatch)
    _mock_stripe(monkeypatch)
    uid = await _subscribe(client, auth, db_session)
    assert (await client.get("/api/billing", headers=auth)).json()["plan"] == "pro"

    resp = await _post_webhook(client, _subscription_event(
        "customer.subscription.deleted", uid))
    assert resp.status_code == 200 and resp.json()["result"] == "synced"

    b = (await client.get("/api/billing", headers=auth)).json()
    assert b["plan"] == "community"
    assert b["status"] == "canceled"


async def test_plan_change_webhook_switches_plan(client, auth, db_session, monkeypatch):
    _configure_stripe(monkeypatch)
    _mock_stripe(monkeypatch)
    uid = await _subscribe(client, auth, db_session, price_id="price_basic")
    assert (await client.get("/api/billing", headers=auth)).json()["plan"] == "basic"

    # user upgrades inside the portal
    await _post_webhook(client, _subscription_event(
        "customer.subscription.updated", uid, price_id="price_pro"))
    assert (await client.get("/api/billing", headers=auth)).json()["plan"] == "pro"

    # …and schedules a cancellation that has not taken effect yet
    await _post_webhook(client, _subscription_event(
        "customer.subscription.updated", uid, status="past_due", price_id="price_pro"))
    b = (await client.get("/api/billing", headers=auth)).json()
    assert b["status"] == "past_due"
    assert b["plan"] == "community"  # not entitled while payment is failing


@pytest.mark.parametrize("etype", ["customer.subscription.updated",
                                   "customer.subscription.deleted"])
async def test_webhook_rejects_bad_signatures(client, monkeypatch, etype):
    _configure_stripe(monkeypatch)
    payload = json.dumps(_subscription_event(etype, "someone")).encode()
    resp = await client.post("/api/billing/webhook", content=payload,
                             headers={"Stripe-Signature": "t=1,v1=deadbeef",
                                      "Content-Type": "application/json"})
    assert resp.status_code == 400
