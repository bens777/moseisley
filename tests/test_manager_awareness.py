"""The omniscient, proactive Manager (Prompt 2).

Three properties, tested against the real objects:
  · SETUP STATE carries the user's whole situation, so the Manager never has to
    ask what the database already knows;
  · the Manager opens the conversation once — and only once — per nudge;
  · a Manager message can only ever produce a whitelisted in-app link.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.agents import actions, nudges
from backend.agents.orchestrator import EmptyArgs, _execute_setup_tool
from backend.core.models import (
    AgentConfig,
    Goal,
    IntegrationConnection,
    Project,
    ScheduledJob,
    TelegramBinding,
    User,
)
from tests.conftest import setup_mock_provider

WEB = Path(__file__).resolve().parents[1] / "apps" / "web"


async def _user(db_session, client, auth) -> User:
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    return await db_session.get(User, uid)


# ── 1. extended setup state ─────────────────────────────────────────

async def test_setup_state_is_empty_but_complete_for_a_new_user(client, auth, db_session):
    user = await _user(db_session, client, auth)
    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())

    assert state["projects"] == {"count": 0, "titles": []}
    assert state["goal_count"] == 0 and state["goal_titles"] == []
    assert state["active_missions"] == 0
    assert state["integrations"] == {"email": False, "calendar": False, "telegram": False,
                                     "demo": False, "connected": []}
    assert state["schedules"]["count"] == 0
    assert state["agents"] == []
    assert state["trial_days_left"] >= 0
    # the pre-existing contract is untouched
    assert state["has_goal"] is False and state["goals"] == []


async def test_setup_state_sees_everything_the_user_built(client, auth, db_session):
    user = await _user(db_session, client, auth)
    db_session.add_all([
        Project(user_id=user.id, name="Cantina relaunch", status="active"),
        Project(user_id=user.id, name="Killed idea", status="killed"),
        Goal(user_id=user.id, title="Reach 5k MRR", metric="mrr", target_value=5000,
             status="active"),
        AgentConfig(user_id=user.id, adapter_type="native", display_name="Vex",
                    configuration_json={"role": "radar"}),
        IntegrationConnection(user_id=user.id, integration_type="google", name="Google",
                              capabilities_json={"gmail.read": "READ",
                                                 "calendar.read": "READ"}),
        IntegrationConnection(user_id=user.id, integration_type="demo", name="Demo data",
                              capabilities_json={}),
        TelegramBinding(user_id=user.id, telegram_user_id="42", telegram_chat_id="42"),
        ScheduledJob(user_id=user.id, job_type="market_radar", interval_seconds=86400,
                     cron_hint="daily 06:00"),
        ScheduledJob(user_id=user.id, job_type="daily_strategist", interval_seconds=86400),
    ])
    await db_session.flush()

    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())

    # killed projects are not "what the user is working on"
    assert state["projects"] == {"count": 1, "titles": ["Cantina relaunch"]}
    assert state["goal_count"] == 1 and state["goal_titles"] == ["Reach 5k MRR"]
    assert state["active_missions"] == 1          # a mission IS an active goal
    assert state["integrations"]["email"] is True
    assert state["integrations"]["calendar"] is True
    assert state["integrations"]["telegram"] is True
    assert state["integrations"]["demo"] is True
    assert state["integrations"]["connected"] == ["demo", "google"]
    assert state["agents"] == [{"name": "Vex", "role": "radar", "enabled": True}]

    schedules = state["schedules"]
    assert schedules["count"] == 2
    by_type = {j["job_type"]: j for j in schedules["jobs"]}
    assert by_type["market_radar"]["role"] == "radar"
    # cadence is described in the user's own timezone, hint or no hint
    assert by_type["market_radar"]["cadence"].startswith("every day at ")
    assert by_type["market_radar"]["cadence"].endswith("(UTC)")
    assert by_type["daily_strategist"]["role"] == "strategist"
    assert by_type["daily_strategist"]["cadence"].startswith("every day at ")
    assert by_type["daily_strategist"]["next_run_at"] is not None


async def test_setup_state_is_json_serializable_for_injection(client, auth, db_session):
    """It is injected as JSON into every Manager turn — it must survive that."""
    user = await _user(db_session, client, auth)
    db_session.add(ScheduledJob(user_id=user.id, job_type="market_radar",
                                interval_seconds=3600))
    await db_session.flush()
    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())
    assert json.loads(json.dumps(state, default=str))["schedules"]["jobs"][0]["cadence"] \
        == "every hour"


# ── 2. proactive nudges ─────────────────────────────────────────────

async def test_zero_project_user_is_nudged_exactly_once(client, auth, db_session):
    first = await client.post("/api/manager/nudge", headers=auth)
    assert first.json()["posted"] is True
    body = first.json()["message"]
    assert "You don't have a project yet" in body["content"]
    assert "(action:projects)" in body["content"]

    # the message is persisted in the one Manager conversation
    msgs = (await client.get("/api/manager/messages", headers=auth)).json()
    assert [m["content"] for m in msgs] == [body["content"]]

    # opening the home again must not repeat it, however many times
    for _ in range(3):
        assert (await client.post("/api/manager/nudge", headers=auth)).json() \
            == {"posted": False}
    msgs = (await client.get("/api/manager/messages", headers=auth)).json()
    assert len(msgs) == 1

    acts = (await client.get("/api/activity", headers=auth)).json()
    assert [e["event_type"] for e in acts].count("manager_nudge_sent") == 1


async def test_answered_nudge_is_never_repeated(client, auth, db_session):
    assert (await client.post("/api/manager/nudge", headers=auth)).json()["posted"] is True
    await setup_mock_provider(client, auth, responses={
        "no thanks": json.dumps({"action": "reply", "text": "Understood."}),
    })
    await client.post("/api/manager/message", headers=auth, json={"text": "no thanks"})

    # the thread moved on; the same nudge must not come back
    assert (await client.post("/api/manager/nudge", headers=auth)).json() == {"posted": False}


async def test_only_one_nudge_is_pending_at_a_time(client, auth, db_session):
    """A crewed user with no goal qualifies for two nudges in a row — they must
    not both land before the user has said anything."""
    user = await _user(db_session, client, auth)
    db_session.add(AgentConfig(user_id=user.id, adapter_type="native",
                               display_name="Vex", configuration_json={"role": "radar"}))
    await db_session.flush()
    await db_session.commit()

    assert (await client.post("/api/manager/nudge", headers=auth)).json()["posted"] is True
    assert (await client.post("/api/manager/nudge", headers=auth)).json() == {"posted": False}
    msgs = (await client.get("/api/manager/messages", headers=auth)).json()
    assert len(msgs) == 1


async def test_nudge_follows_the_state_of_the_setup(client, auth, db_session):
    """With a goal but nothing recurring, the nudge is about the schedule."""
    user = await _user(db_session, client, auth)
    db_session.add(Goal(user_id=user.id, title="Reach 5k MRR", metric="mrr",
                        status="active"))
    await db_session.flush()
    await db_session.commit()

    posted = (await client.post("/api/manager/nudge", headers=auth)).json()
    assert posted["posted"] is True
    assert "nothing runs on its own yet" in posted["message"]["content"]
    assert "(action:schedule)" in posted["message"]["content"]


async def test_fully_set_up_user_is_left_alone(client, auth, db_session):
    user = await _user(db_session, client, auth)
    db_session.add_all([
        Goal(user_id=user.id, title="Reach 5k MRR", metric="mrr", status="active"),
        ScheduledJob(user_id=user.id, job_type="market_radar", interval_seconds=86400),
    ])
    await db_session.flush()
    await db_session.commit()
    assert (await client.post("/api/manager/nudge", headers=auth)).json() == {"posted": False}


# ── 3. clickable actions: whitelist only ────────────────────────────

def test_whitelisted_action_survives_sanitizing():
    text = "Add your key here: [Connections](action:connections)"
    assert actions.sanitize(text) == text
    assert actions.found_in(text) == ["connections"]


def test_unknown_action_id_degrades_to_plain_text():
    out = actions.sanitize("Try [the admin panel](action:admin_panel) now")
    assert out == "Try the admin panel now"
    assert actions.found_in(out) == []


def test_urls_are_never_actions():
    for link in ("[click](https://evil.example/steal)", "[click](/settings)",
                 "[click](javascript:alert(1))", "[click](action:https://evil.example)"):
        assert actions.sanitize(link) == link      # left as inert text
        assert actions.found_in(link) == []


def test_backend_and_web_whitelists_agree():
    """apps/web renders from its own copy — it must be the same list."""
    ts = (WEB / "lib" / "actions.ts").read_text(encoding="utf-8")
    block = ts.split("ACTION_ROUTES: Record<string, string> = {")[1].split("};")[0]
    web = dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block))
    assert web == actions.ACTION_ROUTES


def test_prompt_block_lists_every_action():
    block = actions.prompt_block()
    for route_id, path in actions.ACTION_ROUTES.items():
        assert f"action:{route_id} → {path}" in block


def test_platform_reference_only_uses_whitelisted_actions():
    from backend.agents import crew

    reference = crew.platform_reference()
    ids = {rid for _label, rid in actions.ACTION_PATTERN.findall(reference)}
    assert ids and ids <= set(actions.ACTION_ROUTES)
    # every feature the Manager is expected to explain is described
    for feature in ("Crew Genesis", "X-Ray", "Radar", "Goals", "Projects", "The Bar",
                    "AI modes", "Telegram", "Schedule", "My Data",
                    "Data connections", "Command Center"):
        assert feature in reference


async def test_how_do_i_connect_telegram_answers_with_the_connections_action(client, auth):
    """Prompt-level: the Manager's guidance reaches the user as a real action."""
    await setup_mock_provider(client, auth, responses={
        "connect telegram": json.dumps({"action": "reply", "text":
            "Pair it from Connections and the same conversation follows you there. "
            "[Pair Telegram](action:connections)"}),
    })
    reply = (await client.post("/api/manager/message", headers=auth,
                               json={"text": "how do I connect telegram?"})).json()["reply"]
    assert actions.found_in(reply) == ["connections"]

    stored = (await client.get("/api/manager/messages", headers=auth)).json()[-1]
    assert "[Pair Telegram](action:connections)" in stored["content"]


async def test_manager_reply_cannot_smuggle_an_unknown_route(client, auth):
    await setup_mock_provider(client, auth, responses={
        "admin": json.dumps({"action": "reply", "text":
            "Sure — [open the admin panel](action:admin_panel) or "
            "[read this](https://evil.example)."}),
    })
    reply = (await client.post("/api/manager/message", headers=auth,
                               json={"text": "admin"})).json()["reply"]
    assert "action:admin_panel" not in reply
    assert "open the admin panel" in reply           # the words survive, the link does not
    assert "[read this](https://evil.example)" in reply   # inert: the client never links it


def test_nudge_texts_only_use_whitelisted_actions():
    for kind, text in nudges.TEXTS.items():
        assert actions.sanitize(text) == text, kind
        assert actions.found_in(text), kind
