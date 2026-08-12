"""Manager as setup concierge: it can read the real setup state and configure
the platform through the same gates the dashboard uses — and it can never talk
its way past one.
"""
from __future__ import annotations

import json

import pytest

from backend.agents.orchestrator import EmptyArgs, _execute_setup_tool, _execute_tool
from backend.core.config import get_settings
from backend.core.models import SubscriptionState, User
from tests.conftest import setup_mock_provider


def tool_call(tool: str, args: dict | None = None) -> str:
    return json.dumps({"action": "tool", "tool": tool, "args": args or {}})


def reply(text: str) -> str:
    return json.dumps({"action": "reply", "text": text})


async def _user(db_session, client, auth) -> User:
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    return await db_session.get(User, uid)


def _hosted(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_setup")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")


# ── get_setup_state accuracy ────────────────────────────────────────

async def test_setup_state_reflects_reality(client, auth, db_session):
    user = await _user(db_session, client, auth)
    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())
    assert state["ai_mode"] == "factory" and state["mode_label"] == "ROOKIE"
    assert state["has_goal"] is False
    assert state["connected_providers"] == []
    assert state["orchestrator"]["configured"] is False
    assert state["recommended_models"] == []

    # connect a provider + create a goal, and the state follows
    await setup_mock_provider(client, auth)
    from backend.core.models import Goal

    user = await _user(db_session, client, auth)      # no HTTP calls after this point
    db_session.add(Goal(user_id=user.id, title="Reach 5k MRR", metric="mrr",
                        target_value=5000, status="active"))
    await db_session.flush()
    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())
    assert [p["provider"] for p in state["connected_providers"]] == ["mock"]
    assert state["has_goal"] is True
    assert state["recommended_models"][0]["provider"] == "mock"
    assert state["recommended_models"][0]["recommended_model"]


# ── set_ai_mode: happy path + the gate ──────────────────────────────

async def test_set_ai_mode_happy_path(client, auth, db_session):
    user = await _user(db_session, client, auth)
    await setup_mock_provider(client, auth)          # self-host: expert allowed
    out = await _execute_setup_tool(db_session, user, "setup.set_ai_mode",
                                    type("A", (), {"mode": "expert"})())
    assert out == {"ok": True, "ai_mode": "custom"}
    assert (await _execute_setup_tool(db_session, user, "setup.state",
                                      EmptyArgs()))["ai_mode"] == "custom"


async def test_trial_user_cannot_switch_to_expert(client, auth, db_session, monkeypatch):
    """The Manager gets the reason, not the capability."""
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.set_ai_mode",
                                    type("A", (), {"mode": "expert"})())
    assert out["error"] == "gated"
    assert "[byok_requires_subscription]" in out["detail"]
    assert "subscription" in out["unlocks_with"]
    # nothing was written
    assert (await _execute_setup_tool(db_session, user, "setup.state",
                                      EmptyArgs()))["ai_mode"] == "factory"

    # subscribing unlocks exactly the same call
    from sqlalchemy import select

    row = (await db_session.execute(select(SubscriptionState).where(
        SubscriptionState.user_id == user.id))).scalar_one_or_none()
    if row is None:
        row = SubscriptionState(user_id=user.id)
        db_session.add(row)
    row.status, row.price_id = "active", "price_pro"
    await db_session.flush()
    out = await _execute_setup_tool(db_session, user, "setup.set_ai_mode",
                                    type("A", (), {"mode": "expert"})())
    assert out["ok"] is True


async def test_dev_mode_requires_the_users_own_key(client, auth, db_session):
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.set_ai_mode",
                                    type("A", (), {"mode": "dev"})())
    assert out["error"] == "needs_key" and out["link"] == "/connections"

    from backend.providers import registry as _registry

    await _registry.save_provider(db_session, user.id, "openrouter", "sk-or-user-key")
    await db_session.flush()
    out = await _execute_setup_tool(db_session, user, "setup.set_ai_mode",
                                    type("A", (), {"mode": "dev"})())
    assert out["ok"] is True and out["ai_mode"] == "dev"


async def test_invalid_mode_is_refused(client, auth, db_session):
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.set_ai_mode",
                                    type("A", (), {"mode": "turbo"})())
    assert out["error"] == "invalid_mode"


# ── configure_orchestrator ──────────────────────────────────────────

async def test_configure_orchestrator_happy_path(client, auth, db_session):
    await setup_mock_provider(client, auth)
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.configure_orchestrator",
                                    type("A", (), {"provider": "mock", "model": "mock-1"})())
    assert out == {"ok": True, "provider": "mock", "model": "mock-1"}
    await db_session.commit()
    cfg = (await client.get("/api/orchestrator", headers=auth)).json()
    assert cfg["provider"] == "mock" and cfg["model"] == "mock-1"


async def test_configure_orchestrator_refuses_unconnected_provider(client, auth, db_session):
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.configure_orchestrator",
                                    type("A", (), {"provider": "anthropic",
                                                   "model": "claude-sonnet-5"})())
    assert out["error"] == "not_connected" and out["link"] == "/connections"
    assert "cannot enter keys" in out["detail"]


async def test_configure_orchestrator_refuses_unknown_model(client, auth, db_session):
    await setup_mock_provider(client, auth)
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.configure_orchestrator",
                                    type("A", (), {"provider": "mock",
                                                   "model": "definitely-not-a-model"})())
    assert out["error"] == "unknown_model" and "mock-1" in out["available"]


# ── goal + connection helper ────────────────────────────────────────

async def test_create_goal_reuses_the_compiler(client, auth, db_session):
    await setup_mock_provider(client, auth)
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(
        db_session, user, "setup.create_goal",
        type("A", (), {"title": "Reach 5000 EUR MRR by 2026-10-01",
                       "description": "Solo SaaS, no paid ads."})())
    await db_session.commit()
    assert out.get("ok") or out.get("status")     # created, or a clarifying question
    if out.get("ok"):
        goals = (await client.get("/api/goals", headers=auth)).json()
        assert len(goals) == 1


async def test_suggest_connection_returns_the_deep_link(client, auth, db_session):
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.suggest_connection",
                                    type("A", (), {"provider": "anthropic"})())
    assert out["link"] == "/connections"
    assert "never see or enter API keys" in out["note"]
    bad = await _execute_setup_tool(db_session, user, "setup.suggest_connection",
                                    type("A", (), {"provider": "wizard-ai"})())
    assert bad["error"] == "unknown_provider"


# ── manager-only + end-to-end through the tool loop ─────────────────

@pytest.mark.parametrize("tool", ["setup.state", "setup.set_ai_mode",
                                  "setup.configure_orchestrator", "setup.create_goal",
                                  "setup.suggest_connection"])
async def test_setup_tools_are_manager_only(client, auth, db_session, tool):
    user = await _user(db_session, client, auth)
    out = await _execute_tool(db_session, user, tool, EmptyArgs(), "run-1", role="orchestrator")
    assert out["error"].endswith("only available to the Manager")


async def test_manager_runs_the_setup_sequence(client, auth):
    """'Do the setup' → reads state, sets the brain, and reports what it did."""
    await setup_mock_provider(client, auth, responses={
        "do the setup": tool_call("setup.state"),
        "TOOL RESULT for setup.state": tool_call(
            "setup.configure_orchestrator", {"provider": "mock", "model": "mock-1"}),
        "TOOL RESULT for setup.configure_orchestrator": reply(
            "Done: your Orchestrator now runs on mock-1. Next, give me a goal."),
    })
    resp = await client.post("/api/manager/message", headers=auth,
                             json={"text": "do the setup"})
    assert resp.status_code == 200
    assert "mock-1" in resp.json()["reply"]
    cfg = (await client.get("/api/orchestrator", headers=auth)).json()
    assert cfg["configured"] is True and cfg["model"] == "mock-1"
