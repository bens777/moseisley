"""DEV mode — the user's OWN OpenRouter key, ":free" models only.

Available to everyone (trial, expired, unsubscribed): their key, their quota,
zero platform cost. Internal ai_mode value is "dev"; "factory" (ROOKIE) and
"custom" (EXPERT) keep their exact meanings.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from backend.core.config import get_settings
from backend.core.models import LlmUsage, SubscriptionState, User
from backend.providers import factory_pool, registry

FACTORY_KEY = "sk-or-platform-key"
DEV_FAST = factory_pool.DEV_MODELS[factory_pool.DEFAULT_FAST]


def _enable_factory(monkeypatch):
    monkeypatch.setattr(get_settings(), "factory_openrouter_api_key", FACTORY_KEY)


def _hosted(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_dev")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")


def _openrouter_response(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "gen-dev", "model": model,
        "choices": [{"message": {"content": "dev says hi"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8}})


def _capture_openrouter(monkeypatch, handler=None):
    """Record every OpenRouter call: (auth header, model)."""
    seen: list[tuple[str, str]] = []
    orig_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "openrouter.ai" in str(url):
            payload = kwargs.get("json") or {}
            auth = (kwargs.get("headers") or {}).get("Authorization", "")
            seen.append((auth, payload.get("model", "")))
            if handler is not None:
                return handler(payload)
            return _openrouter_response(payload.get("model", ""))
        return await orig_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return seen


async def _connect_openrouter(client, auth, key: str = "sk-or-user-key"):
    resp = await client.post("/api/providers", headers=auth,
                             json={"provider": "openrouter", "api_key": key})
    assert resp.status_code == 200, resp.text


async def _set_mode(client, auth, mode: str, expect: int = 200):
    resp = await client.patch("/api/settings", headers=auth,
                              json={"settings": {"ai_mode": mode}})
    assert resp.status_code == expect, resp.text
    return resp


async def _uid(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


# ── mode validation + exposure ──────────────────────────────────────

async def test_ai_mode_accepts_the_three_modes(client, auth, monkeypatch):
    _enable_factory(monkeypatch)
    await _connect_openrouter(client, auth)
    for mode in ("factory", "dev", "custom"):
        await _set_mode(client, auth, mode)
    resp = await client.patch("/api/settings", headers=auth,
                              json={"settings": {"ai_mode": "wizard"}})
    assert resp.status_code == 400
    assert "dev" in resp.json()["detail"]


async def test_settings_exposes_mode_and_dev_key(client, auth, monkeypatch):
    _enable_factory(monkeypatch)
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["ai_mode"] == "factory" and s["dev_key_connected"] is False

    await _connect_openrouter(client, auth)
    await _set_mode(client, auth, "dev")
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["ai_mode"] == "dev" and s["dev_key_connected"] is True


# ── routing ─────────────────────────────────────────────────────────

async def test_dev_routes_through_the_users_own_key_with_a_free_model(
        client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)          # platform key exists but must NOT be used
    await _connect_openrouter(client, auth)
    await _set_mode(client, auth, "dev")
    uid = await _uid(client, auth)
    seen = _capture_openrouter(monkeypatch)

    result = await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.commit()
    assert result.text == "dev says hi"

    auth_header, model = seen[0]
    assert auth_header == "Bearer sk-or-user-key"      # their key…
    assert FACTORY_KEY not in auth_header              # …never the platform key
    assert model == DEV_FAST[0] and model.endswith(":free")

    rows = list((await db_session.execute(select(LlmUsage).where(
        LlmUsage.user_id == uid))).scalars())
    assert len(rows) == 1
    assert rows[0].provider == "openrouter"  # NOT "factory" — see fuel test below


async def test_non_free_model_is_coerced_to_the_dev_pool(client, auth, db_session,
                                                         monkeypatch):
    """A paid model configured earlier must never be used on the user's key."""
    _enable_factory(monkeypatch)
    await _connect_openrouter(client, auth)
    await registry.set_orchestrator_config(db_session, await _uid(client, auth),
                                           "openrouter", "anthropic/claude-sonnet-5")
    await db_session.commit()
    await _set_mode(client, auth, "dev")
    uid = await _uid(client, auth)
    seen = _capture_openrouter(monkeypatch)

    await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.commit()
    assert seen[0][1] == DEV_FAST[0]  # silently swapped, no error raised


async def test_free_model_choice_is_honoured(client, auth, db_session, monkeypatch):
    _enable_factory(monkeypatch)
    await _connect_openrouter(client, auth)
    chosen = factory_pool.DEV_MODELS[factory_pool.DEFAULT_REASONING][1]
    await registry.set_orchestrator_config(db_session, await _uid(client, auth),
                                           "openrouter", chosen)
    await db_session.commit()
    await _set_mode(client, auth, "dev")
    seen = _capture_openrouter(monkeypatch)

    await registry.generate(db_session, await _uid(client, auth),
                            [{"role": "user", "content": "hi"}])
    await db_session.commit()
    assert seen[0][1] == chosen and chosen.endswith(":free")


async def test_dev_falls_back_within_the_bucket_on_429(client, auth, db_session,
                                                       monkeypatch):
    _enable_factory(monkeypatch)
    await _connect_openrouter(client, auth)
    await _set_mode(client, auth, "dev")

    def handler(payload):
        if payload["model"] == DEV_FAST[0]:
            return httpx.Response(429, json={"error": "rate limited"})
        return _openrouter_response(payload["model"])

    seen = _capture_openrouter(monkeypatch, handler)
    result = await registry.generate(db_session, await _uid(client, auth),
                                     [{"role": "user", "content": "hi"}])
    await db_session.commit()
    assert result.text == "dev says hi"
    assert [m for _, m in seen] == [DEV_FAST[0], DEV_FAST[1]]


async def test_dev_without_a_key_raises_dev_key_missing(client, auth, db_session,
                                                        monkeypatch):
    _enable_factory(monkeypatch)
    await _set_mode(client, auth, "dev")
    seen = _capture_openrouter(monkeypatch)

    with pytest.raises(registry.DevKeyMissing) as exc:
        await registry.generate(db_session, await _uid(client, auth),
                                [{"role": "user", "content": "hi"}])
    assert "[dev_key_missing]" in str(exc.value)
    assert seen == []
    await db_session.rollback()

    resp = await client.post("/api/chat/message", headers=auth, json={"text": "hi"})
    assert resp.status_code == 424  # NoProviderAvailable handler
    assert "[dev_key_missing]" in resp.json()["detail"]


async def test_dev_works_for_an_expired_trial_user(client, auth, db_session, monkeypatch):
    """No subscription, trial over — DEV still runs on their own key."""
    _enable_factory(monkeypatch)
    _hosted(monkeypatch)
    await _connect_openrouter(client, auth)
    await client.patch("/api/settings", headers=auth, json={"settings": {
        "trial_started_at": (datetime.now(UTC) - timedelta(days=60)).isoformat()}})
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["tier"] == "expired" and s["byok_allowed"] is False

    await _set_mode(client, auth, "dev")
    seen = _capture_openrouter(monkeypatch)
    result = await registry.generate(db_session, await _uid(client, auth),
                                     [{"role": "user", "content": "hi"}])
    await db_session.commit()
    assert result.text == "dev says hi"
    assert seen[0][0] == "Bearer sk-or-user-key"


async def test_dev_traffic_is_not_counted_as_rookie_fuel(client, auth, db_session,
                                                         monkeypatch):
    _enable_factory(monkeypatch)
    monkeypatch.setattr(get_settings(), "factory_trial_daily_requests", 2)
    await _connect_openrouter(client, auth)
    await _set_mode(client, auth, "dev")
    uid = await _uid(client, auth)
    _capture_openrouter(monkeypatch)

    for _ in range(4):  # twice the ROOKIE cap
        await registry.generate(db_session, uid, [{"role": "user", "content": "hi"}])
    await db_session.commit()

    user = await db_session.get(User, uid)
    assert await registry.factory_fuel_used_today(db_session, user) == 0
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["factory"]["fuel_used_today"] == 0


# ── gating carve-out ────────────────────────────────────────────────

async def test_openrouter_connectable_without_subscription_others_gated(
        client, auth, monkeypatch):
    _hosted(monkeypatch)
    resp = await client.post("/api/providers", headers=auth,
                             json={"provider": "openai", "api_key": "sk-x"})
    assert resp.status_code == 402  # still subscriber-only

    resp = await client.post("/api/providers", headers=auth,
                             json={"provider": "openrouter", "api_key": "sk-or-user-key"})
    assert resp.status_code == 200  # DEV carve-out

    # …and can be toggled back on without a pass
    await client.post("/api/providers/openrouter/toggle", headers=auth,
                      json={"enabled": False})
    resp = await client.post("/api/providers/openrouter/toggle", headers=auth,
                             json={"enabled": True})
    assert resp.status_code == 200


async def test_expert_mode_still_subscriber_gated_with_a_dev_key(client, auth, db_session,
                                                                 monkeypatch):
    """An OpenRouter key does not unlock EXPERT — routing stays the real gate."""
    _enable_factory(monkeypatch)
    _hosted(monkeypatch)
    await _connect_openrouter(client, auth)
    await _set_mode(client, auth, "custom", expect=402)

    # subscribing unlocks it
    uid = await _uid(client, auth)
    db_session.add(SubscriptionState(user_id=uid, status="active", price_id="price_pro"))
    await db_session.commit()
    await _set_mode(client, auth, "custom")
    s = (await client.get("/api/settings", headers=auth)).json()
    assert s["ai_mode"] == "custom" and s["byok_allowed"] is True
