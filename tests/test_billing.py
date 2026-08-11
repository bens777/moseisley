"""Owner directive §79: Stripe Billing in test mode — signature verification, webhook
lifecycle, entitlement sync. No real charges; Stripe API calls are fixture-mocked."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx

from backend.billing.stripe_billing import verify_webhook_signature
from backend.core.config import get_settings

WEBHOOK_SECRET = "whsec_test_secret_123"


def sign(payload: bytes, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def test_signature_verification():
    payload = b'{"type":"x"}'
    assert verify_webhook_signature(payload, sign(payload), WEBHOOK_SECRET)
    # wrong secret
    assert not verify_webhook_signature(payload, sign(payload, "whsec_other"), WEBHOOK_SECRET)
    # tampered payload
    assert not verify_webhook_signature(b'{"type":"y"}', sign(payload), WEBHOOK_SECRET)
    # expired timestamp
    old = sign(payload, ts=int(time.time()) - 4000)
    assert not verify_webhook_signature(payload, old, WEBHOOK_SECRET)
    # garbage header
    assert not verify_webhook_signature(payload, "not-a-signature", WEBHOOK_SECRET)


def _subscription_event(etype: str, user_id: str, status: str = "active",
                        cancel_at_period_end: bool = False,
                        price_id: str = "price_test_premium") -> dict:
    return {
        "type": etype,
        "data": {"object": {
            "id": "sub_123", "customer": "cus_123", "status": status,
            "cancel_at_period_end": cancel_at_period_end,
            "current_period_end": int(time.time()) + 30 * 86400,
            "items": {"data": [{"price": {"id": price_id}}]},
            "metadata": {"moseisley_user_id": user_id},
        }},
    }


async def _post_webhook(client, event: dict):
    payload = json.dumps(event).encode()
    return await client.post("/api/billing/webhook", content=payload,
                             headers={"Stripe-Signature": sign(payload),
                                      "Content-Type": "application/json"})


async def test_webhook_lifecycle_and_entitlement(client, auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", WEBHOOK_SECRET)
    me = (await client.get("/api/me", headers=auth)).json()

    # community by default; entitlement never comes from the browser
    state = (await client.get("/api/billing", headers=auth)).json()
    assert state["plan"] == "community"

    # unsigned/bad-signature webhooks are rejected
    resp = await client.post("/api/billing/webhook", content=b"{}",
                             headers={"Stripe-Signature": "t=1,v1=bad"})
    assert resp.status_code == 400

    # subscription created → active → premium entitlement
    resp = await _post_webhook(client, _subscription_event(
        "customer.subscription.created", me["id"]))
    assert resp.json()["result"] == "synced"
    state = (await client.get("/api/billing", headers=auth)).json()
    assert state["plan"] == "pro" and state["status"] == "active"

    # past_due → entitlement withdrawn
    await _post_webhook(client, _subscription_event(
        "customer.subscription.updated", me["id"], status="past_due"))
    state = (await client.get("/api/billing", headers=auth)).json()
    assert state["plan"] == "community" and state["status"] == "past_due"

    # cancel_at_period_end visible while still active
    await _post_webhook(client, _subscription_event(
        "customer.subscription.updated", me["id"], status="active",
        cancel_at_period_end=True))
    state = (await client.get("/api/billing", headers=auth)).json()
    assert state["plan"] == "pro" and state["cancel_at_period_end"] is True

    # deletion → canceled
    await _post_webhook(client, _subscription_event(
        "customer.subscription.deleted", me["id"]))
    state = (await client.get("/api/billing", headers=auth)).json()
    assert state["plan"] == "community" and state["status"] == "canceled"

    # ledger recorded the transitions
    acts = (await client.get("/api/activity", headers=auth)).json()
    changes = [e for e in acts if e["event_type"] == "subscription_changed"]
    assert len(changes) >= 3


async def test_checkout_and_portal(client, auth, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_api_key", "sk_test_123")
    monkeypatch.setattr(settings, "stripe_price_id", "price_test_premium")

    orig_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        u, data = str(url), kwargs.get("data") or {}
        if "api.stripe.com" not in u:
            return await orig_post(self, url, **kwargs)
        if u.endswith("/customers"):
            return httpx.Response(200, json={"id": "cus_new"})
        if u.endswith("/checkout/sessions"):
            assert data["mode"] == "subscription"
            assert data["line_items[0][price]"] == "price_test_premium"
            return httpx.Response(200, json={"url": "https://checkout.stripe.com/test"})
        if u.endswith("/billing_portal/sessions"):
            return httpx.Response(200, json={"url": "https://billing.stripe.com/test"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    resp = await client.post("/api/billing/checkout", headers=auth)
    assert resp.json()["url"].startswith("https://checkout.stripe.com/")
    resp = await client.post("/api/billing/portal", headers=auth)
    assert resp.json()["url"].startswith("https://billing.stripe.com/")


async def test_checkout_unconfigured_is_externally_blocked(client, auth):
    resp = await client.post("/api/billing/checkout", headers=auth)
    assert resp.status_code == 424  # truthful: not configured, not faked


async def test_basic_pro_plan_derivation_and_gating(client, auth, monkeypatch):
    """Final pricing: Basic $9 vs Pro $19. Plan derives from the Stripe-synced
    price id; Pro-only features return 402 for hosted Basic users."""
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "stripe_api_key", "sk_test_123")
    monkeypatch.setattr(settings, "stripe_price_id_basic", "price_test_basic")
    monkeypatch.setattr(settings, "stripe_price_id_pro", "price_test_pro")
    me = (await client.get("/api/me", headers=auth)).json()

    # basic subscription → basic plan → Pro features blocked with 402
    await _post_webhook(client, _subscription_event(
        "customer.subscription.created", me["id"], price_id="price_test_basic"))
    state = (await client.get("/api/billing", headers=auth)).json()
    assert state["plan"] == "basic"
    resp = await client.post("/api/xray/run", headers=auth,
                             json={"horizon_days": 90})
    assert resp.status_code == 402
    resp = await client.post("/api/strategist/run", headers=auth)
    assert resp.status_code == 402
    # core (Basic) functionality stays available
    resp = await client.get("/api/goals", headers=auth)
    assert resp.status_code == 200

    # upgrade to pro → gates open (request proceeds past entitlement check)
    await _post_webhook(client, _subscription_event(
        "customer.subscription.updated", me["id"], price_id="price_test_pro"))
    state = (await client.get("/api/billing", headers=auth)).json()
    assert state["plan"] == "pro"
    resp = await client.post("/api/strategist/run", headers=auth)
    assert resp.status_code != 402


async def test_checkout_plan_selection(client, auth, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_api_key", "sk_test_123")
    monkeypatch.setattr(settings, "stripe_price_id_basic", "price_test_basic")
    monkeypatch.setattr(settings, "stripe_price_id_pro", "price_test_pro")

    seen: list[str] = []
    orig_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        u, data = str(url), kwargs.get("data") or {}
        if "api.stripe.com" not in u:
            return await orig_post(self, url, **kwargs)
        if u.endswith("/customers"):
            return httpx.Response(200, json={"id": "cus_new"})
        if u.endswith("/checkout/sessions"):
            seen.append(data["line_items[0][price]"])
            return httpx.Response(200, json={"url": "https://checkout.stripe.com/test"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    resp = await client.post("/api/billing/checkout", headers=auth, json={"plan": "basic"})
    assert resp.status_code == 200
    resp = await client.post("/api/billing/checkout", headers=auth, json={"plan": "pro"})
    assert resp.status_code == 200
    assert seen == ["price_test_basic", "price_test_pro"]
    resp = await client.post("/api/billing/checkout", headers=auth, json={"plan": "enterprise"})
    assert resp.status_code == 422


async def test_billing_tenancy(client, auth, monkeypatch):
    from tests.conftest import auth_headers

    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", WEBHOOK_SECRET)
    me = (await client.get("/api/me", headers=auth)).json()
    await _post_webhook(client, _subscription_event(
        "customer.subscription.created", me["id"]))
    h_b = await auth_headers(client, "billb@example.com")
    state_b = (await client.get("/api/billing", headers=h_b)).json()
    assert state_b["plan"] == "community"
