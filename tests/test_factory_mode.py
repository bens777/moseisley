"""Factory mode: platform OpenRouter key, 14-day trial → paid → expired.

Covers: effective ai_mode + PATCH validation, tier resolution (paid > trial >
expired, lazy trial start), zero-config factory routing, CrewConfig-custom
precedence, model fallback on 429, non-retryable errors, credits-exhausted
402 → FactoryServiceUnavailable, trial/paid daily caps, FactoryTrialExpired,
the orchestrator gate in factory mode, and key non-exposure."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from backend.core.config import get_settings
from backend.core.models import LlmUsage, SubscriptionState
from backend.providers import factory_pool, registry
from tests.conftest import setup_mock_provider

FACTORY_KEY = "sk-or-factory-test-key"
FAST = factory_pool.FACTORY_MODELS[factory_pool.DEFAULT_FAST]


def _enable_factory(monkeypatch):
    monkeypatch.setattr(get_settings(), "factory_openrouter_api_key", FACTORY_KEY)


def _disable_factory(monkeypatch):
    """Pin the platform key OFF — never inherit the deployment's real .env."""
    monkeypatch.setattr(get_settings(), "factory_openrouter_api_key", None)


def _make_paid(monkeypatch):
    """Configure Stripe + return kwargs for an active Pro subscription row."""
    monkeypatch.setattr(get_settings(), "stripe_api_key", "sk_test_x")
    monkeypatch.setattr(get_settings(), "stripe_price_id_pro", "price_pro_test")
    return {"status": "active", "price_id": "price_pro_test"}


def _completion_response(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "gen-1", "model": model,
        "choices": [{"message": {"content": "factory says hi"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                  "cost": 0.0002},
    })


def _patch_openrouter(monkeypatch, handler):
    """Route httpx POSTs: openrouter → handler; everything else → real post."""
    orig_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "openrouter.ai" in str(url):
            return handler(kwargs.get("json") or {})
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


async def _user_id(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


async def _expire_trial(client, auth):
    started = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    resp = await client.patch("/api/settings", headers=auth,
                              json={"settings": {"trial_started_at": started}})
    assert resp.status_code == 200


# ── effective ai_mode + validation ──────────────────────────────────

async def test_effective_ai_mode_and_patch_validation(client, auth, monkeypatch):
    _enable_factory(monkeypatch)
    # fresh user, no connections → factory, full trial ahead
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["ai_mode"] == "factory"
    assert s["factory"]["available"] is True
    assert s["factory"]["tier"] == "trial"
    assert s["factory"]["trial_days_left"] == get_settings().factory_trial_days
    assert s["factory"]["fuel_used_today"] == 0
    assert s["factory"]["fuel_cap"] == get_settings().factory_trial_daily_requests
    assert s["factory"]["has_provider_connections"] is False

    # connecting a provider flips the computed default to custom
    await setup_mock_provider(client, auth)
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["ai_mode"] == "custom"

    # explicit setting wins over the computed default
    resp = await client.patch("/api/settings", headers=auth,
                              json={"settings": {"ai_mode": "factory"}})
    assert resp.status_code == 200
    assert (await client.get("/api/settings", headers=auth)).json()["ai_mode"] == "factory"

    # invalid value rejected server-side
    resp = await client.patch("/api/settings", headers=auth,
                              json={"settings": {"ai_mode": "turbo"}})
    assert resp.status_code == 400


async def test_factory_unavailable_without_key(client, auth, monkeypatch):
    _disable_factory(monkeypatch)
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"] == {"available": False}


# ── factory routing ─────────────────────────────────────────────────

async def test_factory_routing_zero_config(client, auth, db_session, monkeypatch):
    """A brand-new user with NO provider rows gets a working generation through
    the platform key: one usage row, factory marker, lazy trial start."""
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    seen: list[str] = []

    def handler(payload):
        seen.append(payload["model"])
        assert payload["usage"] == {"include": True}  # openrouter cost accounting kept
        return _completion_response(payload["model"])

    _patch_openrouter(monkeypatch, handler)
    result = await registry.generate(db_session, uid,
                                     [{"role": "user", "content": "hello"}])
    await db_session.commit()
    assert result.text == "factory says hi"
    assert seen == [FAST[0]]  # first candidate served

    rows = list((await db_session.execute(select(LlmUsage).where(
        LlmUsage.user_id == uid))).scalars())
    assert len(rows) == 1
    assert rows[0].provider == "factory"  # marker → exact cap counting
    assert rows[0].requested_model == FAST[0]
    assert rows[0].cost_source == "PROVIDER_REPORTED"

    # first factory call lazily stamped the trial start
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["settings"].get("trial_started_at")
    assert s["factory"]["fuel_used_today"] == 1


async def test_crew_custom_config_still_wins_in_factory_mode(client, auth, db_session,
                                                             monkeypatch):
    _enable_factory(monkeypatch)
    await setup_mock_provider(client, auth, responses={"ping": "mock wins"})
    await client.patch("/api/settings", headers=auth,
                       json={"settings": {"ai_mode": "factory"}})
    resp = await client.put("/api/crew/radar/model-policy", headers=auth, json={
        "model_policy": "custom", "provider": "mock", "model": "mock-1"})
    assert resp.status_code == 200, resp.text
    uid = await _user_id(client, auth)

    _patch_openrouter(monkeypatch, lambda payload: _completion_response(payload["model"]))
    # radar has an explicit custom config → mock wins even in factory mode
    result = await registry.generate(db_session, uid,
                                     [{"role": "user", "content": "ping"}],
                                     crew_role="radar")
    assert result.text == "mock wins"
    # a role without custom config routes through the factory
    result = await registry.generate(db_session, uid,
                                     [{"role": "user", "content": "hello"}],
                                     crew_role="strategist")
    assert result.text == "factory says hi"
    await db_session.commit()
    providers = sorted(r.provider for r in (await db_session.execute(
        select(LlmUsage).where(LlmUsage.user_id == uid))).scalars())
    assert providers == ["factory", "mock"]


async def test_custom_mode_has_zero_factory_behavior(client, auth, db_session, monkeypatch):
    """In custom mode the platform key is never used — existing BYOK paths only."""
    _enable_factory(monkeypatch)
    await setup_mock_provider(client, auth, responses={"hello": "byok reply"})

    def handler(payload):  # any openrouter call would be a factory leak
        raise AssertionError("factory key must not be used in custom mode")

    _patch_openrouter(monkeypatch, handler)
    uid = await _user_id(client, auth)
    result = await registry.generate(db_session, uid,
                                     [{"role": "user", "content": "hello"}])
    assert result.text == "byok reply"


# ── fallback + provider errors ──────────────────────────────────────

async def test_factory_fallback_on_429(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    attempts: list[str] = []

    def handler(payload):
        attempts.append(payload["model"])
        if payload["model"] == FAST[0]:
            return httpx.Response(429, json={"error": "rate limited"})
        return _completion_response(payload["model"])

    _patch_openrouter(monkeypatch, handler)
    result = await registry.generate(db_session, uid,
                                     [{"role": "user", "content": "hello"}])
    await db_session.commit()
    assert result.text == "factory says hi"
    assert attempts == [FAST[0], FAST[1]]  # walked to the fallback model

    rows = list((await db_session.execute(select(LlmUsage).where(
        LlmUsage.user_id == uid))).scalars())
    assert len(rows) == 1  # exactly ONE usage row despite the retry
    assert rows[0].requested_model == FAST[0]  # first choice recorded
    assert rows[0].model == FAST[1]            # actually-served model recorded


async def test_factory_non_retryable_error_propagates(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    attempts: list[str] = []

    def handler(payload):
        attempts.append(payload["model"])
        return httpx.Response(401, json={"error": "bad key"})  # not retryable

    _patch_openrouter(monkeypatch, handler)
    with pytest.raises(Exception) as exc_info:
        await registry.generate(db_session, uid, [{"role": "user", "content": "hello"}])
    assert "401" in str(exc_info.value)
    assert len(attempts) == 1  # no fallback on auth errors


async def test_credits_exhausted_402_maps_to_service_unavailable(client, auth, db_session,
                                                                 monkeypatch):
    """Prepaid credits dry (402 on both candidates) → calm typed error, HTTP 429
    with the machine-readable code — never a raw crash (§5)."""
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    attempts: list[str] = []

    def handler(payload):
        attempts.append(payload["model"])
        return httpx.Response(402, json={"error": "insufficient credits"})

    _patch_openrouter(monkeypatch, handler)
    with pytest.raises(registry.FactoryServiceUnavailable):
        await registry.generate(db_session, uid, [{"role": "user", "content": "hello"}])
    assert attempts == [FAST[0], FAST[1]]  # 402 is retryable, then mapped
    await db_session.rollback()

    resp = await client.post("/api/chat/message", headers=auth, json={"text": "hi"})
    assert resp.status_code == 429
    assert "[factory_service_unavailable]" in resp.json()["detail"]
    assert "recharging" in resp.json()["detail"]


# ── tiers ───────────────────────────────────────────────────────────

async def test_trial_expiry_cuts_factory_ai(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    await _expire_trial(client, auth)

    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["tier"] == "expired"
    assert s["factory"]["trial_days_left"] == 0

    def handler(payload):
        raise AssertionError("no provider call for an expired trial")

    _patch_openrouter(monkeypatch, handler)
    with pytest.raises(registry.FactoryTrialExpired):
        await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.rollback()

    resp = await client.post("/api/chat/message", headers=auth, json={"text": "hi"})
    assert resp.status_code == 429
    assert "[factory_trial_expired]" in resp.json()["detail"]


async def test_paid_subscription_restores_factory_ai(client, auth, db_session, monkeypatch):
    """An active Basic/Pro subscription outranks an expired trial."""
    _enable_factory(monkeypatch)
    uid = await _user_id(client, auth)
    await _expire_trial(client, auth)
    db_session.add(SubscriptionState(user_id=uid, **_make_paid(monkeypatch)))
    await db_session.commit()

    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["tier"] == "paid"
    # the fixture subscribes on the Pro price → Pro's bigger tank
    assert s["factory"]["fuel_cap"] == get_settings().factory_pro_daily_requests

    _patch_openrouter(monkeypatch, lambda payload: _completion_response(payload["model"]))
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    assert result.text == "factory says hi"


# ── daily caps ──────────────────────────────────────────────────────

async def test_trial_daily_cap(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    monkeypatch.setattr(get_settings(), "factory_trial_daily_requests", 3)
    uid = await _user_id(client, auth)
    for _ in range(3):
        db_session.add(LlmUsage(user_id=uid, provider="factory", model=FAST[0],
                                requested_model=FAST[0], purpose="chat", status="success"))
    await db_session.commit()

    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_used_today"] == 3

    def handler(payload):
        raise AssertionError("no provider call once fuel is exhausted")

    _patch_openrouter(monkeypatch, handler)
    with pytest.raises(registry.FactoryFuelExhausted) as exc_info:
        await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    assert isinstance(exc_info.value, registry.LlmBudgetExceeded)  # → HTTP 429 handler
    await db_session.rollback()

    resp = await client.post("/api/chat/message", headers=auth, json={"text": "hi"})
    assert resp.status_code == 429
    assert "[factory_fuel_exhausted]" in resp.json()["detail"]
    assert "bigger tank" in resp.json()["detail"]  # trial users see the upgrade hint


async def test_paid_cap_is_higher_and_byok_usage_not_counted(client, auth, db_session,
                                                             monkeypatch):
    _enable_factory(monkeypatch)
    monkeypatch.setattr(get_settings(), "factory_trial_daily_requests", 3)
    uid = await _user_id(client, auth)
    db_session.add(SubscriptionState(user_id=uid, **_make_paid(monkeypatch)))
    # over the TRIAL cap: 3 factory rows + BYOK rows that must not count
    for _ in range(3):
        db_session.add(LlmUsage(user_id=uid, provider="factory", model=FAST[0],
                                requested_model=FAST[0], purpose="chat", status="success"))
    for _ in range(5):
        db_session.add(LlmUsage(user_id=uid, provider="openrouter", model=FAST[0],
                                requested_model=FAST[0], purpose="chat", status="success"))
    await db_session.commit()

    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_used_today"] == 3  # BYOK openrouter rows excluded

    _patch_openrouter(monkeypatch, lambda payload: _completion_response(payload["model"]))
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    assert result.text == "factory says hi"  # paid cap (300) not reached


# ── orchestrator gate ───────────────────────────────────────────────

async def test_orchestrator_gate_in_factory_mode(client, auth, monkeypatch):
    # without the platform key: gate keeps its historic behavior
    _disable_factory(monkeypatch)
    resp = await client.put("/api/orchestrator", headers=auth, json={
        "provider": "openrouter", "model": "anthropic/claude-sonnet-5"})
    assert resp.status_code == 400

    # with the key + factory mode: selectable with zero ProviderConnection rows
    _enable_factory(monkeypatch)
    resp = await client.put("/api/orchestrator", headers=auth, json={
        "provider": "openrouter", "model": "anthropic/claude-sonnet-5"})
    assert resp.status_code == 200, resp.text
    cfg = (await client.get("/api/orchestrator", headers=auth)).json()
    assert cfg["configured"] is True and cfg["provider"] == "openrouter"

    # expired trial closes the gate again
    await _expire_trial(client, auth)
    resp = await client.put("/api/orchestrator", headers=auth, json={
        "provider": "openrouter", "model": "anthropic/claude-sonnet-5"})
    assert resp.status_code == 400


async def test_factory_key_never_exposed(client, auth, monkeypatch):
    _enable_factory(monkeypatch)
    blob = json.dumps((await client.get("/api/settings", headers=auth)).json())
    blob += json.dumps((await client.get("/api/providers", headers=auth)).json())
    blob += json.dumps((await client.get("/api/providers/definitions", headers=auth)).json())
    assert FACTORY_KEY not in blob
