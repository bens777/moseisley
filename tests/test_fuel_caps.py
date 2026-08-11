"""Plan-sized factory fuel caps: trial 40 / Basic 150 / Pro 400.

The bigger tank is part of what Pro sells, so the cap follows the Stripe-synced
plan. Unknown plan strings fall back to the Basic cap (least privilege), and the
pre-split FACTORY_PAID_DAILY_REQUESTS still configures both plans.
"""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from backend.core.config import Settings, get_settings
from backend.core.models import LlmUsage, SubscriptionState
from backend.providers import factory_pool, registry

FACTORY_KEY = "sk-or-factory-test-key"
FAST = factory_pool.FACTORY_MODELS[factory_pool.DEFAULT_FAST]


def _enable_factory(monkeypatch):
    monkeypatch.setattr(get_settings(), "factory_openrouter_api_key", FACTORY_KEY)


def _stripe_configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_caps")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")


async def _subscribe(client, auth, db_session, price_id: str):
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    db_session.add(SubscriptionState(user_id=uid, status="active", price_id=price_id))
    await db_session.commit()
    return uid


# ── cap selection ───────────────────────────────────────────────────

@pytest.mark.parametrize("plan,expected", [
    ("pro", 400),
    ("basic", 150),
    ("community", 150),      # unknown/other on a paid tier → basic cap
    ("enterprise-2027", 150),
    (None, 150),
])
def test_paid_cap_follows_plan_with_basic_fallback(plan, expected):
    assert factory_pool.daily_cap_for_plan(factory_pool.TIER_PAID, plan) == expected


def test_trial_and_expired_use_the_trial_cap():
    assert factory_pool.daily_cap_for_plan(factory_pool.TIER_TRIAL) == 40
    assert factory_pool.daily_cap_for_plan(factory_pool.TIER_TRIAL, "pro") == 40


async def test_settings_exposes_plan_specific_cap(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    _stripe_configured(monkeypatch)

    # trial user
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["tier"] == "trial"
    assert s["factory"]["fuel_cap"] == 40

    # Basic subscriber
    uid = await _subscribe(client, auth, db_session, "price_basic")
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["tier"] == "paid"
    assert s["factory"]["fuel_cap"] == 150

    # upgrading to Pro widens the tank
    row = (await db_session.execute(select(SubscriptionState).where(
        SubscriptionState.user_id == uid))).scalar_one()
    row.price_id = "price_pro"
    await db_session.commit()
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_cap"] == 400


async def test_basic_cap_enforced_while_pro_keeps_running(client, auth, db_session,
                                                          monkeypatch):
    """At 150 used requests a Basic user is out of fuel; a Pro user is not."""
    _enable_factory(monkeypatch)
    _stripe_configured(monkeypatch)
    monkeypatch.setattr(get_settings(), "factory_basic_daily_requests", 3)
    monkeypatch.setattr(get_settings(), "factory_pro_daily_requests", 5)
    uid = await _subscribe(client, auth, db_session, "price_basic")
    for _ in range(3):
        db_session.add(LlmUsage(user_id=uid, provider="factory", model=FAST[0],
                                requested_model=FAST[0], purpose="chat", status="success"))
    await db_session.commit()

    orig_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "openrouter.ai" in str(url):
            return httpx.Response(200, json={
                "id": "gen-1", "model": (kwargs.get("json") or {}).get("model"),
                "choices": [{"message": {"content": "factory says hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(registry.FactoryFuelExhausted):
        await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.rollback()

    # same usage, Pro plan → still inside the bigger tank
    row = (await db_session.execute(select(SubscriptionState).where(
        SubscriptionState.user_id == uid))).scalar_one()
    row.price_id = "price_pro"
    await db_session.commit()
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.commit()
    assert result.text == "factory says hi"


# ── deprecated env var ──────────────────────────────────────────────

def test_deprecated_paid_env_var_configures_both_plans(monkeypatch, caplog):
    monkeypatch.setenv("FACTORY_PAID_DAILY_REQUESTS", "222")
    with caplog.at_level("WARNING", logger="mychief.config"):
        s = Settings()
    assert s.factory_basic_daily_requests == 222
    assert s.factory_pro_daily_requests == 222
    assert "deprecated" in caplog.text.lower()


def test_without_the_deprecated_var_the_split_defaults_apply(monkeypatch):
    monkeypatch.delenv("FACTORY_PAID_DAILY_REQUESTS", raising=False)
    s = Settings()
    assert (s.factory_trial_daily_requests, s.factory_basic_daily_requests,
            s.factory_pro_daily_requests) == (40, 150, 400)
