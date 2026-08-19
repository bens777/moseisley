"""Provider spend policy (§1, §6-§8): FREE_ONLY/PAID_ALLOWED are the only
user-facing choices; ASK_BEFORE_SPENDING is a real, enforced, but hidden
third state. Enforced server-side against ANY capability KNOWN to cost
money — Grok/xAI X search (no free tier) and, critically, LLM calls
including OpenRouter itself once a non-free model is involved — not just
toggled in the UI."""
from __future__ import annotations

import pytest

from backend.core.models import User
from backend.providers import registry, usage_policy
from tests.conftest import auth_headers


async def _connect_xai(client, headers):
    resp = await client.post("/api/providers", json={
        "provider": "xai", "api_key": "xai-secret-abcdef",
    }, headers=headers)
    assert resp.status_code == 200


async def _set_policy_directly(db_session, user_id: str, policy: str) -> None:
    """Bypasses the API on purpose — used only for ASK_BEFORE_SPENDING, which
    §1 hides from the user-facing PUT /providers/policy endpoint."""
    user = await db_session.get(User, user_id)
    usage_policy.set_policy(user, policy)
    await db_session.commit()


# ── §1: ASK_BEFORE_SPENDING is hidden, not deleted ──────────────────────

async def test_default_policy_is_free_only(client, auth):
    resp = await client.get("/api/providers/policy", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["policy"] == "free_only"


async def test_options_only_offer_the_two_user_facing_policies(client, auth):
    resp = await client.get("/api/providers/policy", headers=auth)
    assert set(resp.json()["options"]) == {"free_only", "paid_allowed"}
    assert "ask_before_spending" not in resp.json()["options"]


async def test_put_rejects_ask_before_spending_even_though_it_still_works_internally(client, auth):
    """The API layer hides it (§1); the enum/enforcement is not deleted."""
    resp = await client.put("/api/providers/policy",
                            json={"policy": "ask_before_spending"}, headers=auth)
    assert resp.status_code == 400
    assert usage_policy.ASK_BEFORE_SPENDING in usage_policy.POLICIES  # extension point intact


async def test_policy_roundtrip(client, auth):
    put = await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=auth)
    assert put.status_code == 200
    assert put.json() == {"policy": "paid_allowed"}
    get = await client.get("/api/providers/policy", headers=auth)
    assert get.json()["policy"] == "paid_allowed"


async def test_invalid_policy_rejected(client, auth):
    resp = await client.put("/api/providers/policy", json={"policy": "yolo"}, headers=auth)
    assert resp.status_code == 400


async def test_usage_policy_tenant_isolation(client, db_session):
    headers_a = await auth_headers(client, "policy-a@example.com")
    headers_b = await auth_headers(client, "policy-b@example.com")
    await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=headers_a)
    b_policy = await client.get("/api/providers/policy", headers=headers_b)
    assert b_policy.json()["policy"] == "free_only"


# ── default is flat FREE_ONLY, deliberately not inferred from ai_mode ───

async def test_expert_mode_user_still_defaults_to_free_only(client, auth):
    """§6/§7: connecting a paid key (ai_mode=custom/EXPERT) is not itself
    permission to spend it — that would reopen exactly the loophole this
    policy exists to close. The default is flat, for everyone."""
    await client.patch("/api/settings", json={"settings": {"ai_mode": "custom"}}, headers=auth)
    resp = await client.get("/api/providers/policy", headers=auth)
    assert resp.json()["policy"] == "free_only"


async def test_an_explicit_choice_persists_regardless_of_ai_mode(client, auth):
    await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=auth)
    await client.patch("/api/settings", json={"settings": {"ai_mode": "custom"}}, headers=auth)
    resp = await client.get("/api/providers/policy", headers=auth)
    assert resp.json()["policy"] == "paid_allowed"


# ── x_search (Grok/xAI, no free tier) ────────────────────────────────────

async def test_free_only_blocks_x_search(client, auth, db_session):
    await _connect_xai(client, auth)
    me = (await client.get("/api/me", headers=auth)).json()
    with pytest.raises(usage_policy.PaidCapabilityBlocked):
        await registry.generate_with_x_search(db_session, me["id"], "who is trending")


async def test_paid_allowed_permits_x_search(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=auth)
    me = (await client.get("/api/me", headers=auth)).json()

    import httpx

    async def fake_post(self, url, **kwargs):
        return httpx.Response(200, json={
            "id": "resp-1", "model": "grok-3-mini",
            "output": [{"content": [{"type": "output_text", "text": "trend: ai"}]}],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await registry.generate_with_x_search(db_session, me["id"], "who is trending")
    assert result["text"] == "trend: ai"
    assert result["mock"] is False


async def test_ask_before_spending_blocks_x_search_with_distinct_error(client, auth, db_session):
    await _connect_xai(client, auth)
    me = (await client.get("/api/me", headers=auth)).json()
    await _set_policy_directly(db_session, me["id"], usage_policy.ASK_BEFORE_SPENDING)
    with pytest.raises(usage_policy.ApprovalRequired):
        await registry.generate_with_x_search(db_session, me["id"], "who is trending")


async def test_mock_x_search_path_is_never_blocked_by_policy(client, auth, db_session):
    """The mock/offline fallback (no real xAI key) is free and must stay usable
    under FREE_ONLY — the policy only gates the REAL, paid xAI call."""
    resp = await client.post("/api/providers", json={"provider": "mock", "api_key": "mock"},
                             headers=auth)
    assert resp.status_code == 200
    me = (await client.get("/api/me", headers=auth)).json()
    result = await registry.generate_with_x_search(db_session, me["id"], "who is trending")
    assert result["mock"] is True


def test_paid_capability_blocked_is_registered_as_a_402():
    """The app must map PaidCapabilityBlocked/ApprovalRequired to HTTP responses
    at the FastAPI layer, not rely on every call site to catch it individually —
    that's what makes this a backend policy rather than a per-endpoint habit."""
    from backend.api.app import create_app

    app = create_app()
    assert usage_policy.PaidCapabilityBlocked in app.exception_handlers
    assert usage_policy.ApprovalRequired in app.exception_handlers


# ── §6/§7/§8: FREE_ONLY must protect OpenRouter (and every LLM provider)
# itself, not only non-LLM Intelligence Sources ─────────────────────────

async def _connect_openrouter_as_expert(client, headers, model: str) -> str:
    """EXPERT mode with an explicit, non-":free" orchestrator model — the
    exact shape of a real paid OpenRouter call."""
    await client.post("/api/providers", json={
        "provider": "openrouter", "api_key": "sk-or-v1-real-secret",
    }, headers=headers)
    await client.patch("/api/settings", json={"settings": {"ai_mode": "custom"}}, headers=headers)
    await client.put("/api/orchestrator", json={"provider": "openrouter", "model": model}, headers=headers)
    return (await client.get("/api/me", headers=headers)).json()["id"]


async def test_free_only_plus_openrouter_free_model_is_allowed(client, auth, db_session, monkeypatch):
    import httpx

    await client.post("/api/providers", json={
        "provider": "openrouter", "api_key": "sk-or-v1-real-secret",
    }, headers=auth)
    await client.patch("/api/settings", json={"settings": {"ai_mode": "dev"}}, headers=auth)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]

    async def fake_post(self, url, **kwargs):
        return httpx.Response(200, json={
            "model": "nvidia/nemotron-3.5-lightning:free",
            "choices": [{"message": {"role": "assistant", "content": "ready"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "ping"}],
                                     crew_role="orchestrator")
    assert result.text == "ready"


async def test_free_only_blocks_openrouter_paid_model(client, auth, db_session):
    uid = await _connect_openrouter_as_expert(client, auth, "anthropic/claude-sonnet-5")
    # ai_mode=custom would default the policy to paid_allowed (compat rule) —
    # pin FREE_ONLY explicitly to test the boundary the compat rule doesn't cover.
    await client.put("/api/providers/policy", json={"policy": "free_only"}, headers=auth)
    with pytest.raises(usage_policy.PaidCapabilityBlocked):
        await registry.generate(db_session, uid, [{"role": "user", "content": "ping"}],
                                crew_role="orchestrator")


@pytest.mark.skip(
    reason="depends on GET /api/providers/openrouter/status (is_free_tier), which "
           "ships with the OpenRouter OAuth/tier-detection feature — deferred to a "
           "later Community export pass alongside openrouter_oauth.py (see "
           "docs/community-export.md follow-up notes). The core FREE_ONLY boundary "
           "this suite otherwise covers is exercised by the sibling tests above.")
async def test_free_only_blocks_openrouter_even_with_account_credits(client, auth, db_session, monkeypatch):
    """§7: account_has_credits != permission_to_spend. An upgraded-tier
    OpenRouter account (is_free_tier=False) must not bypass FREE_ONLY."""
    import httpx

    uid = await _connect_openrouter_as_expert(client, auth, "anthropic/claude-sonnet-5")
    await client.put("/api/providers/policy", json={"policy": "free_only"}, headers=auth)

    orig_get = httpx.AsyncClient.get
    completions_reached = False

    async def fake_get(self, url, **kwargs):
        if "openrouter.ai/api/v1/key" in str(url):
            return httpx.Response(200, json={"data": {"is_free_tier": False}})
        return await orig_get(self, url, **kwargs)

    async def fake_post(self, url, **kwargs):
        nonlocal completions_reached
        if "chat/completions" in str(url):
            completions_reached = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    status = await client.get("/api/providers/openrouter/status", headers=auth)
    assert status.json()["is_free_tier"] is False  # upgraded tier, per OpenRouter — not spend permission

    with pytest.raises(usage_policy.PaidCapabilityBlocked):
        await registry.generate(db_session, uid, [{"role": "user", "content": "ping"}],
                                crew_role="orchestrator")
    assert completions_reached is False  # blocked before any real provider call


async def test_paid_allowed_permits_openrouter_paid_model(client, auth, db_session, monkeypatch):
    import httpx

    uid = await _connect_openrouter_as_expert(client, auth, "anthropic/claude-sonnet-5")
    await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=auth)

    async def fake_post(self, url, **kwargs):
        return httpx.Response(200, json={
            "model": "anthropic/claude-sonnet-5",
            "choices": [{"message": {"role": "assistant", "content": "ready"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "ping"}],
                                     crew_role="orchestrator")
    assert result.text == "ready"


async def test_free_only_blocks_any_other_paid_llm_provider(client, auth, db_session):
    """Anthropic has no verifiable free tier at all — fail closed."""
    await client.post("/api/providers", json={
        "provider": "anthropic", "api_key": "sk-ant-secret",
    }, headers=auth)
    await client.patch("/api/settings", json={"settings": {"ai_mode": "custom"}}, headers=auth)
    await client.put("/api/providers/policy", json={"policy": "free_only"}, headers=auth)
    await client.put("/api/orchestrator", json={
        "provider": "anthropic", "model": "claude-sonnet-5"}, headers=auth)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]

    with pytest.raises(usage_policy.PaidCapabilityBlocked):
        await registry.generate(db_session, uid, [{"role": "user", "content": "ping"}],
                                crew_role="orchestrator")


async def test_mock_provider_is_never_blocked_by_free_only(client, auth, db_session):
    from tests.conftest import setup_mock_provider

    await setup_mock_provider(client, auth)
    await client.put("/api/providers/policy", json={"policy": "free_only"}, headers=auth)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    result = await registry.generate(db_session, uid, [{"role": "user", "content": "ping"}],
                                     crew_role="orchestrator")
    assert result.text  # mock always answers; never gated
