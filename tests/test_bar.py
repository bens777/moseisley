"""The Bar — one-time drink purchases that top up factory fuel.

Covers: menu open/closed, checkout session shape, gift validation against the
Friends directory, webhook crediting + idempotency, the consumption order
(daily allowance → purchased fuel → error), expired trials running on
purchased fuel, BYOK never touching the balance, and balance exposure.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest
from sqlalchemy import select

from backend.billing import stripe_billing
from backend.core.config import get_settings
from backend.core.models import Event, LlmUsage, SubscriptionState, User
from backend.providers import factory_pool, registry
from tests.conftest import auth_headers, setup_mock_provider

WEBHOOK_SECRET = "whsec_bar_test"
FACTORY_KEY = "sk-or-factory-test-key"
FAST = factory_pool.FACTORY_MODELS[factory_pool.DEFAULT_FAST]
BOB = {"handle": "bob", "display_name": "Bob", "bio": "Runs a cantina side project."}


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _open_bar(monkeypatch):
    """Stripe configured + all three one-time prices present."""
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_bar")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")
    monkeypatch.setattr(s, "stripe_webhook_secret", WEBHOOK_SECRET)
    bar = stripe_billing.bar_settings()
    monkeypatch.setattr(bar, "stripe_price_id_ale", "price_ale")
    monkeypatch.setattr(bar, "stripe_price_id_punch", "price_punch")
    monkeypatch.setattr(bar, "stripe_price_id_round", "price_round")


def _close_bar(monkeypatch):
    bar = stripe_billing.bar_settings()
    for field in ("stripe_price_id_ale", "stripe_price_id_punch", "stripe_price_id_round"):
        monkeypatch.setattr(bar, field, None)


def _enable_factory(monkeypatch):
    monkeypatch.setattr(get_settings(), "factory_openrouter_api_key", FACTORY_KEY)


def _mock_stripe(monkeypatch, capture: list | None = None):
    """Intercept Stripe REST calls — no network, no charges."""
    async def fake_post(path: str, data: dict) -> dict:
        if capture is not None:
            capture.append((path, data))
        if path == "/customers":
            return {"id": "cus_bar"}
        return {"id": "cs_test_1", "url": "https://checkout.stripe.test/cs_test_1"}

    monkeypatch.setattr(stripe_billing, "_stripe_post", fake_post)


def _bar_event(user_id: str, sku: str, session_id: str = "cs_test_1",
               gift_to: str | None = None, gift_name: str = "") -> dict:
    meta = {"moseisley_user_id": user_id, "bar_sku": sku,
            "bar_fuel": str(stripe_billing.BAR_BY_SKU[sku]["fuel"])}
    if gift_to:
        meta["bar_gift_to"] = gift_to
        meta["bar_gift_to_name"] = gift_name
    return {"type": "checkout.session.completed",
            "data": {"object": {"id": session_id, "mode": "payment",
                                "payment_status": "paid", "customer": "cus_bar",
                                "metadata": meta}}}


async def _post_webhook(client, event: dict):
    payload = json.dumps(event).encode()
    return await client.post("/api/billing/webhook", content=payload,
                             headers={"Stripe-Signature": _sign(payload),
                                      "Content-Type": "application/json"})


async def _user_id(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


def _completion(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "gen-1", "model": model,
        "choices": [{"message": {"content": "factory says hi"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                  "cost": 0.0002}})


def _patch_openrouter(monkeypatch, handler):
    orig_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "openrouter.ai" in str(url):
            return handler(kwargs.get("json") or {})
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


async def _burn_daily_allowance(db_session, user_id: str, count: int):
    for _ in range(count):
        db_session.add(LlmUsage(user_id=user_id, provider="factory", model=FAST[0],
                                requested_model=FAST[0], purpose="chat", status="success"))
    await db_session.commit()


# ── menu ────────────────────────────────────────────────────────────

async def test_bar_closed_when_price_ids_unset(client, auth, monkeypatch):
    _close_bar(monkeypatch)
    menu = (await client.get("/api/billing/bar/menu", headers=auth)).json()
    assert menu["open"] is False
    assert "closed" in menu["closed_reason"].lower()
    assert [i["sku"] for i in menu["items"]] == [
        "nebula_ale", "purple_tentacle_punch", "buy_a_round"]
    assert all(i["available"] is False for i in menu["items"])
    # ordering anyway is refused, cleanly (no crash, no fake success)
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "nebula_ale"})
    assert resp.status_code == 424


async def test_bar_menu_open_shows_prices_and_balance(client, auth, monkeypatch):
    _open_bar(monkeypatch)
    menu = (await client.get("/api/billing/bar/menu", headers=auth)).json()
    assert menu["open"] is True and menu["fuel_balance"] == 0
    prices = {i["sku"]: (i["price_usd"], i["fuel"], i["gift"]) for i in menu["items"]}
    assert prices == {"nebula_ale": (2, 50, False),
                      "purple_tentacle_punch": (5, 150, False),
                      "buy_a_round": (5, 100, True)}


# ── checkout ────────────────────────────────────────────────────────

async def test_checkout_creates_one_time_session(client, auth, monkeypatch):
    _open_bar(monkeypatch)
    calls: list = []
    _mock_stripe(monkeypatch, calls)
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "purple_tentacle_punch"})
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://checkout.stripe.test/")
    _, data = next(c for c in calls if c[0] == "/checkout/sessions")
    assert data["mode"] == "payment"  # one-time, never a subscription
    assert data["line_items[0][price]"] == "price_punch"
    assert data["metadata[bar_sku]"] == "purple_tentacle_punch"
    assert data["metadata[bar_fuel]"] == "150"
    assert "bar" in data["success_url"]


async def test_unknown_sku_rejected(client, auth, monkeypatch):
    _open_bar(monkeypatch)
    _mock_stripe(monkeypatch)
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "bantha_milk"})
    assert resp.status_code == 422


async def test_round_requires_a_real_friend(client, auth, monkeypatch):
    _open_bar(monkeypatch)
    _mock_stripe(monkeypatch)
    # no recipient
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "buy_a_round"})
    assert resp.status_code == 422

    # unknown handle
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "buy_a_round", "friend_id": "nobody"})
    assert resp.status_code == 404

    # existing but UNPUBLISHED profile is not discoverable → not giftable
    bob = await auth_headers(client, "bob@example.com")
    await client.put("/api/friends/me", headers=bob, json=BOB)
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "buy_a_round", "friend_id": "bob"})
    assert resp.status_code == 404

    # published → giftable, with the recipient in the session metadata
    await client.post("/api/friends/me/publish", headers=bob, json={"published": True})
    calls: list = []
    _mock_stripe(monkeypatch, calls)
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "buy_a_round", "friend_id": "bob"})
    assert resp.status_code == 200, resp.text
    _, data = next(c for c in calls if c[0] == "/checkout/sessions")
    assert data["metadata[bar_gift_to]"] == await _user_id(client, bob)
    assert data["metadata[bar_gift_to_name]"] == "Bob"


async def test_friend_id_only_valid_for_a_round(client, auth, monkeypatch):
    _open_bar(monkeypatch)
    _mock_stripe(monkeypatch)
    resp = await client.post("/api/billing/bar/checkout", headers=auth,
                             json={"sku": "nebula_ale", "friend_id": "bob"})
    assert resp.status_code == 422


# ── webhook crediting ───────────────────────────────────────────────

async def test_webhook_credits_fuel_and_is_idempotent(client, auth, db_session, monkeypatch):
    _open_bar(monkeypatch)
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)

    resp = await _post_webhook(client, _bar_event(uid, "nebula_ale"))
    assert resp.status_code == 200 and resp.json()["result"] == "credited"
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_balance"] == 50

    # replayed webhook (Stripe retries) must not credit twice
    resp = await _post_webhook(client, _bar_event(uid, "nebula_ale"))
    assert resp.json()["result"] == "duplicate"
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_balance"] == 50

    # a different session credits again, and balances accumulate
    resp = await _post_webhook(client, _bar_event(uid, "purple_tentacle_punch",
                                                  session_id="cs_test_2"))
    assert resp.json()["result"] == "credited"
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_balance"] == 200

    # purchases are recorded in the append-only ledger, keyed by session id
    events = list((await db_session.execute(select(Event).where(
        Event.user_id == uid, Event.event_type == "bar_purchase"))).scalars())
    assert sorted(e.entity_id for e in events) == ["cs_test_1", "cs_test_2"]


async def test_unpaid_session_credits_nothing(client, auth, monkeypatch):
    _open_bar(monkeypatch)
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    event = _bar_event(uid, "nebula_ale")
    event["data"]["object"]["payment_status"] = "unpaid"
    resp = await _post_webhook(client, event)
    assert resp.json()["result"] == "unpaid"
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_balance"] == 0


async def test_round_credits_the_friend_not_the_buyer(client, auth, db_session, monkeypatch):
    _open_bar(monkeypatch)
    _enable_factory(monkeypatch)
    buyer_id = await _user_id(client, auth)
    # buyer has a published profile → the recipient sees a real name
    await client.put("/api/friends/me", headers=auth,
                     json={"handle": "alice", "display_name": "Alice"})
    await client.post("/api/friends/me/publish", headers=auth, json={"published": True})
    bob = await auth_headers(client, "bob@example.com")
    bob_id = await _user_id(client, bob)

    resp = await _post_webhook(client, _bar_event(buyer_id, "buy_a_round",
                                                  gift_to=bob_id, gift_name="Bob"))
    assert resp.json()["result"] == "gift_credited"

    # recipient got the fuel + a dismissible gift flag; buyer got none
    s_bob = (await client.get("/api/settings", headers=bob)).json()
    assert s_bob["factory"]["fuel_balance"] == 100
    assert s_bob["settings"]["bar_gift_pending"] == {"from": "Alice", "fuel": 100}
    s_buyer = (await client.get("/api/settings", headers=auth)).json()
    assert s_buyer["factory"]["fuel_balance"] == 0

    # both sides are in the ledger, and a replay is still a no-op
    types = sorted(e.event_type for e in (await db_session.execute(
        select(Event).where(Event.entity_id == "cs_test_1"))).scalars())
    assert types == ["bar_gift_received", "bar_gift_sent"]
    resp = await _post_webhook(client, _bar_event(buyer_id, "buy_a_round",
                                                  gift_to=bob_id, gift_name="Bob"))
    assert resp.json()["result"] == "duplicate"
    s_bob = (await client.get("/api/settings", headers=bob)).json()
    assert s_bob["factory"]["fuel_balance"] == 100

    # the recipient can dismiss the banner through the existing PATCH
    await client.patch("/api/settings", headers=bob,
                       json={"settings": {"bar_gift_pending": None}})
    s_bob = (await client.get("/api/settings", headers=bob)).json()
    assert s_bob["settings"]["bar_gift_pending"] is None


# ── consumption order ───────────────────────────────────────────────

async def test_consumption_order_cap_then_balance_then_error(client, auth, db_session,
                                                             monkeypatch):
    _enable_factory(monkeypatch)
    monkeypatch.setattr(get_settings(), "factory_trial_daily_requests", 2)
    uid = await _user_id(client, auth)
    _patch_openrouter(monkeypatch, lambda p: _completion(p["model"]))

    # 1. within the daily allowance → purchased fuel untouched
    user = await db_session.get(User, uid)
    factory_pool.credit_fuel(user, 2)
    await db_session.commit()
    await registry.generate(db_session, uid, [{"role": "user", "content": "one"}])
    await db_session.commit()
    await db_session.refresh(user)
    assert factory_pool.get_fuel_balance(user) == 2  # allowance covered it

    # 2. allowance exhausted → the balance is spent instead of raising
    await _burn_daily_allowance(db_session, uid, 2)
    await registry.generate(db_session, uid, [{"role": "user", "content": "two"}])
    await db_session.commit()
    await db_session.refresh(user)
    assert factory_pool.get_fuel_balance(user) == 1
    await registry.generate(db_session, uid, [{"role": "user", "content": "three"}])
    await db_session.commit()
    await db_session.refresh(user)
    assert factory_pool.get_fuel_balance(user) == 0

    # 3. allowance AND balance gone → the themed error, no provider call
    _patch_openrouter(monkeypatch, lambda p: pytest.fail("no call once fuel is gone"))
    with pytest.raises(registry.FactoryFuelExhausted):
        await registry.generate(db_session, uid, [{"role": "user", "content": "four"}])
    await db_session.rollback()
    resp = await client.post("/api/chat/message", headers=auth, json={"text": "hi"})
    assert resp.status_code == 429
    assert "[factory_fuel_exhausted]" in resp.json()["detail"]


async def test_expired_trial_runs_on_purchased_fuel(client, auth, db_session, monkeypatch):
    """A user whose trial ended can buy a drink and keep working."""
    _open_bar(monkeypatch)
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    await client.patch("/api/settings", headers=auth, json={"settings": {
        "trial_started_at": "2020-01-01T00:00:00+00:00"}})
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["tier"] == "expired"

    # expired + no balance → trial-expired error
    with pytest.raises(registry.FactoryTrialExpired):
        await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.rollback()

    # a drink revives the crew, and every call draws down the balance
    resp = await _post_webhook(client, _bar_event(uid, "nebula_ale"))
    assert resp.json()["result"] == "credited"
    _patch_openrouter(monkeypatch, lambda p: _completion(p["model"]))
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.commit()
    assert result.text == "factory says hi"
    user = await db_session.get(User, uid)
    await db_session.refresh(user)
    assert factory_pool.get_fuel_balance(user) == 49


async def test_paid_tier_allowance_is_used_before_purchased_fuel(client, auth, db_session,
                                                                 monkeypatch):
    _enable_factory(monkeypatch)
    monkeypatch.setattr(get_settings(), "stripe_api_key", "sk_test_bar")
    monkeypatch.setattr(get_settings(), "stripe_price_id_pro", "price_pro")
    uid = await _user_id(client, auth)
    db_session.add(SubscriptionState(user_id=uid, status="active", price_id="price_pro"))
    user = await db_session.get(User, uid)
    factory_pool.credit_fuel(user, 10)
    await db_session.commit()

    _patch_openrouter(monkeypatch, lambda p: _completion(p["model"]))
    await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.commit()
    await db_session.refresh(user)
    assert factory_pool.get_fuel_balance(user) == 10  # paid allowance covered it


async def test_byok_mode_never_touches_purchased_fuel(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    await setup_mock_provider(client, auth, responses={"hello": "byok reply"})
    uid = await _user_id(client, auth)
    user = await db_session.get(User, uid)
    factory_pool.credit_fuel(user, 5)
    await db_session.commit()

    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hello"}])
    await db_session.commit()
    assert result.text == "byok reply"
    await db_session.refresh(user)
    assert factory_pool.get_fuel_balance(user) == 5  # untouched by BYOK traffic
