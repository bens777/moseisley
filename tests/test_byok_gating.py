"""BYOK is a subscriber feature on the hosted platform, free when self-hosting.

Hosted (Stripe configured): the 14-day trial runs on platform AI only —
connecting provider keys and switching to custom mode require Basic/Pro.
Self-host (Stripe unconfigured): zero gating, exactly as before. Rows created
before this rule are never deleted or disabled; routing simply stays factory
until the user subscribes.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.core.config import get_settings
from backend.core.models import ProviderConnection, SubscriptionState
from backend.providers import registry
from tests.conftest import setup_mock_provider

FACTORY_KEY = "sk-or-factory-test-key"


def _hosted(monkeypatch):
    """Stripe configured → billing (and therefore gating) is enforced."""
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_hosted")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")


def _self_host(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", None)
    monkeypatch.setattr(s, "stripe_price_id_basic", None)
    monkeypatch.setattr(s, "stripe_price_id_pro", None)
    monkeypatch.setattr(s, "stripe_price_id", None)


def _enable_factory(monkeypatch):
    monkeypatch.setattr(get_settings(), "factory_openrouter_api_key", FACTORY_KEY)


async def _subscribe(client, auth, db_session, plan_price: str = "price_pro"):
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    row = (await db_session.execute(select(SubscriptionState).where(
        SubscriptionState.user_id == uid))).scalar_one_or_none()
    if row is None:
        row = SubscriptionState(user_id=uid)
        db_session.add(row)
    row.status = "active"
    row.price_id = plan_price
    await db_session.commit()
    return uid


# ── provider connections ────────────────────────────────────────────

async def test_trial_user_cannot_connect_keys_when_hosted(client, auth, db_session, monkeypatch):
    _hosted(monkeypatch)
    resp = await client.post("/api/providers", headers=auth,
                             json={"provider": "openai", "api_key": "sk-user-key"})
    assert resp.status_code == 402
    assert "[byok_requires_subscription]" in resp.json()["detail"]
    # nothing was written
    rows = list((await db_session.execute(select(ProviderConnection))).scalars())
    assert rows == []


async def test_self_host_has_no_gating(client, auth, monkeypatch):
    _self_host(monkeypatch)
    resp = await client.post("/api/providers", headers=auth,
                             json={"provider": "openai", "api_key": "sk-user-key"})
    assert resp.status_code == 200
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["byok_allowed"] is True


async def test_subscriber_can_connect_keys(client, auth, db_session, monkeypatch):
    _hosted(monkeypatch)
    await _subscribe(client, auth, db_session)
    resp = await client.post("/api/providers", headers=auth,
                             json={"provider": "openai", "api_key": "sk-user-key"})
    assert resp.status_code == 200, resp.text


async def test_trial_user_cannot_re_enable_a_key_but_can_disable(client, auth, db_session,
                                                                 monkeypatch):
    """Keys created before the rule stay put; disabling is always allowed."""
    _self_host(monkeypatch)
    await setup_mock_provider(client, auth)  # connected while self-hosted
    _hosted(monkeypatch)                     # deployment becomes hosted

    resp = await client.post("/api/providers/mock/toggle", headers=auth,
                             json={"enabled": False})
    assert resp.status_code == 200  # turning something off is never gated
    resp = await client.post("/api/providers/mock/toggle", headers=auth,
                             json={"enabled": True})
    assert resp.status_code == 402
    assert "[byok_requires_subscription]" in resp.json()["detail"]

    # the row itself is untouched — never deleted, never purged server-side
    rows = list((await db_session.execute(select(ProviderConnection))).scalars())
    assert len(rows) == 1 and rows[0].provider == "mock"


# ── ai_mode ─────────────────────────────────────────────────────────

async def test_patch_custom_mode_blocked_for_trial_allowed_for_subscriber(
        client, auth, db_session, monkeypatch):
    _hosted(monkeypatch)
    resp = await client.patch("/api/settings", headers=auth,
                              json={"settings": {"ai_mode": "custom"}})
    assert resp.status_code == 402
    assert "[byok_requires_subscription]" in resp.json()["detail"]
    # switching back to factory is always fine
    assert (await client.patch("/api/settings", headers=auth,
                               json={"settings": {"ai_mode": "factory"}})).status_code == 200

    await _subscribe(client, auth, db_session)
    resp = await client.patch("/api/settings", headers=auth,
                              json={"settings": {"ai_mode": "custom"}})
    assert resp.status_code == 200


async def test_byok_allowed_in_settings_for_each_audience(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    # hosted trial → locked
    _hosted(monkeypatch)
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["byok_allowed"] is False and s["ai_mode"] == "factory"

    # hosted subscriber → unlocked
    await _subscribe(client, auth, db_session, plan_price="price_basic")
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["byok_allowed"] is True

    # self-host → unlocked regardless of subscription state
    _self_host(monkeypatch)
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["byok_allowed"] is True


# ── routing ─────────────────────────────────────────────────────────

async def test_preexisting_custom_setting_still_routes_factory(client, auth, db_session,
                                                               monkeypatch):
    """A trial user who set ai_mode=custom (and connected a key) before this
    patch keeps both — but their crew runs on factory AI until they subscribe,
    and flips to their own key the instant they do."""
    _enable_factory(monkeypatch)
    _self_host(monkeypatch)
    await setup_mock_provider(client, auth, responses={"hello": "byok reply"})
    await client.patch("/api/settings", headers=auth,
                       json={"settings": {"ai_mode": "custom"}})
    uid = (await client.get("/api/me", headers=auth)).json()["id"]

    _hosted(monkeypatch)  # now a hosted trial user with legacy custom settings
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["byok_allowed"] is False
    assert s["ai_mode"] == "factory"                        # reported as factory…
    assert s["settings"]["ai_mode"] == "custom"             # …but never rewritten

    import httpx
    orig_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "openrouter.ai" in str(url):
            return httpx.Response(200, json={
                "id": "gen-1", "model": (kwargs.get("json") or {}).get("model"),
                "choices": [{"message": {"content": "factory says hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hello"}])
    assert result.text == "factory says hi"  # factory, not the connected mock key

    # subscribing restores their own key immediately — no re-entry needed
    await _subscribe(client, auth, db_session)
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hello"}])
    await db_session.commit()
    assert result.text == "byok reply"


async def test_byok_allowed_helper_matches_plan(client, auth, db_session, monkeypatch):
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    _self_host(monkeypatch)
    assert await registry.byok_allowed(db_session, uid) is True
    _hosted(monkeypatch)
    assert await registry.byok_allowed(db_session, uid) is False
    await _subscribe(client, auth, db_session)
    db_session.expire_all()
    assert await registry.byok_allowed(db_session, uid) is True


@pytest.mark.parametrize("status", ["canceled", "past_due", "none"])
async def test_inactive_subscription_does_not_unlock_byok(client, auth, db_session,
                                                          monkeypatch, status):
    _hosted(monkeypatch)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    db_session.add(SubscriptionState(user_id=uid, status=status, price_id="price_pro"))
    await db_session.commit()
    assert await registry.byok_allowed(db_session, uid) is False
