"""Skills: manifests that compose existing primitives, and nothing else.

Two families of test here. The first checks the LIFECYCLE — enable composes,
disable reverses, gates hold. The second checks the MANIFESTS against the live
code, because a skill that names a role the platform cannot actually drive is a
lie the user only discovers when nothing happens at 07:30.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from backend.agents import crew
from backend.agents.orchestrator import EmptyArgs, _execute_setup_tool, _execute_tool
from backend.billing import entitlements
from backend.core.config import get_settings
from backend.core.models import CrewConfig, ScheduledJob, SubscriptionState, User
from backend.jobs import handlers as job_handlers  # noqa: F401 — registers the handlers
from backend.jobs.scheduler import HANDLERS
from backend.skills import catalog
from backend.skills import service as skills_svc
from backend.strategy import autopilot
from tests.conftest import setup_mock_provider


async def _user(db_session, client, auth) -> User:
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    return await db_session.get(User, uid)


def _hosted(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_api_key", "sk_test_skills")
    monkeypatch.setattr(s, "stripe_price_id_basic", "price_basic")
    monkeypatch.setattr(s, "stripe_price_id_pro", "price_pro")


async def _subscribe(db_session, user: User, price_id: str) -> None:
    db_session.add(SubscriptionState(
        user_id=user.id, status="active", price_id=price_id,
        stripe_customer_id="cus_test", stripe_subscription_id="sub_test"))
    await db_session.flush()
    await db_session.commit()


async def _jobs(db_session, user_id: str, job_type: str | None = None) -> list[ScheduledJob]:
    q = select(ScheduledJob).where(ScheduledJob.user_id == user_id)
    if job_type:
        q = q.where(ScheduledJob.job_type == job_type)
    return list((await db_session.execute(q)).scalars())


# ── manifest honesty: every claim checked against the live code ─────

def test_every_skill_id_and_shape_is_sane():
    assert len(catalog.CATALOG) == 4
    assert {s.id for s in catalog.CATALOG} == {
        "inbox-triage", "market-watch", "follow-up-chaser", "x-ray-monthly"}
    for s in catalog.CATALOG:
        assert s.name and s.category and s.one_liner
        assert s.what_it_does and s.requirements
        assert s.roles or s.schedules, s.id


def test_every_named_role_exists():
    for s in catalog.CATALOG:
        for role in s.roles:
            assert role in crew.ROLES, (s.id, role)


def test_every_scheduled_job_type_has_a_handler():
    """A schedule entry pointing at a job type with no handler would fail on
    every run with 'no handler for job type'."""
    for s in catalog.CATALOG:
        for entry in s.schedules:
            assert entry.job_type in HANDLERS, (s.id, entry.job_type)


def test_roles_are_driven_by_a_mechanism_that_can_actually_run_them():
    """The trap: crew.DELEGATABLE and autopilot.RUNNERS do not overlap. A role
    scheduled through the wrong one silently no-ops forever."""
    assert set(crew.DELEGATABLE).isdisjoint(autopilot.RUNNERS)

    for s in catalog.CATALOG:
        loops = {e.payload.get("loop") for e in s.schedules if e.job_type == "autopilot"}
        for loop in loops:
            assert loop in autopilot.RUNNERS, (s.id, loop)
            assert loop in s.roles, (s.id, loop)
        for entry in s.instructions:
            assert entry.assigned_role in crew.DELEGATABLE, (s.id, entry.assigned_role)
            assert entry.kind in __import__(
                "backend.ops.instructions", fromlist=["KINDS"]).KINDS


def test_gating_matches_the_roles_the_skill_enables():
    """If a skill turns on a Pro role, it must require that role's feature."""
    for s in catalog.CATALOG:
        for role in s.roles:
            feature = entitlements.ROLE_FEATURES.get(role)
            if feature:
                assert feature in s.features, (s.id, role, feature)
        if s.schedules:
            # every scheduled handler refuses to run without this on hosted
            assert catalog.SCHEDULED_AUTONOMY in s.features, s.id


def test_manifests_disclose_the_pro_requirement_in_words():
    for s in catalog.CATALOG:
        if catalog.SCHEDULED_AUTONOMY in s.features:
            assert any("Pro" in r for r in s.requirements), s.id


def test_email_skills_say_they_need_an_email_source():
    """And that there is no sample data standing in for one."""
    for skill_id in ("inbox-triage", "follow-up-chaser"):
        text = " ".join(catalog.BY_ID[skill_id].requirements).lower()
        assert "email source" in text and "google" in text
        assert "demo" not in text and "synthetic" not in text


def test_monthly_xray_admits_it_is_every_30_days():
    """The scheduler counts seconds; it has no idea what a month is."""
    skill = catalog.BY_ID["x-ray-monthly"]
    assert skill.schedules[0].interval_seconds == 30 * 86400
    assert any("30 days" in r for r in skill.requirements)


def test_reference_block_lists_every_skill_and_only_whitelisted_actions():
    from backend.agents import actions

    block = catalog.reference_block()
    for s in catalog.CATALOG:
        assert s.name in block and f"`{s.id}`" in block
    ids = {rid for _label, rid in actions.ACTION_PATTERN.findall(block)}
    assert ids and ids <= set(actions.ACTION_ROUTES)
    assert actions.ACTION_ROUTES["skills"] == "/skills"


# ── enable composes through the existing primitives ─────────────────

async def test_enable_turns_on_the_role_and_creates_the_schedule(client, auth, db_session):
    user = await _user(db_session, client, auth)
    resp = await client.post("/api/skills/inbox-triage/enable", headers=auth, json={})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    cfg = await crew.get_config(db_session, user.id, "inbox_triage")
    assert cfg is not None and cfg.enabled is True

    jobs = await _jobs(db_session, user.id, "autopilot")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.payload_json == {"loop": "inbox_triage"}
    assert job.interval_seconds == 86400
    assert job.status == "scheduled" and job.next_run_at is not None
    assert job.idempotency_key == f"skill:inbox-triage:0:{user.id}"


async def test_enable_honours_the_requested_run_time(client, auth, db_session):
    user = await _user(db_session, client, auth)
    await client.post("/api/skills/inbox-triage/enable", headers=auth,
                      json={"config": {"run_time": "06:45"}})
    job = (await _jobs(db_session, user.id, "autopilot"))[0]
    assert job.next_run_at.strftime("%H:%M") == "06:45"      # user timezone is UTC


async def test_multi_job_skill_staggers_its_jobs(client, auth, db_session):
    user = await _user(db_session, client, auth)
    await client.post("/api/skills/follow-up-chaser/enable", headers=auth, json={})
    jobs = sorted(await _jobs(db_session, user.id, "autopilot"), key=lambda j: j.next_run_at)
    assert [j.payload_json["loop"] for j in jobs] == ["follow_up", "commitment_tracker"]
    assert jobs[0].next_run_at != jobs[1].next_run_at
    for role in ("follow_up", "commitment_tracker"):
        cfg = await crew.get_config(db_session, user.id, role)
        assert cfg is not None and cfg.enabled is True


async def test_instruction_backed_skill_creates_a_real_instruction(client, auth, db_session,
                                                                   monkeypatch):
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    await _subscribe(db_session, user, "price_pro")

    await client.post("/api/skills/x-ray-monthly/enable", headers=auth, json={})

    instructions = (await client.get("/api/instructions", headers=auth)).json()
    assert len(instructions) == 1
    ins = instructions[0]
    assert ins["created_by"] == "skill" and ins["assigned_role"] == "xray"
    assert ins["enabled"] is True

    job = (await _jobs(db_session, user.id, "instruction_run"))[0]
    assert job.payload_json["instruction_id"] == ins["id"]
    assert job.interval_seconds == 30 * 86400


async def test_shared_key_skill_adopts_the_built_in_sweep_instead_of_duplicating_it(
        client, auth, db_session, monkeypatch):
    """The platform already gives everyone a daily Radar sweep. Enabling Market
    Watch must not create a second one."""
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    await _subscribe(db_session, user, "price_pro")
    await client.get("/api/today", headers=auth)          # seeds the built-in sweep
    before = await _jobs(db_session, user.id, "market_radar")
    assert len(before) == 1

    await client.post("/api/skills/market-watch/enable", headers=auth, json={})
    after = await _jobs(db_session, user.id, "market_radar")
    assert len(after) == 1 and after[0].id == before[0].id


async def test_double_enable_is_idempotent(client, auth, db_session):
    user = await _user(db_session, client, auth)
    for _ in range(3):
        assert (await client.post("/api/skills/inbox-triage/enable",
                                  headers=auth, json={})).status_code == 200
    assert len(await _jobs(db_session, user.id, "autopilot")) == 1

    events = [e["event_type"] for e in (await client.get("/api/activity", headers=auth)).json()]
    assert events.count("skill_enabled") == 1


async def test_unknown_skill_is_a_404(client, auth):
    assert (await client.post("/api/skills/make-me-rich/enable",
                              headers=auth, json={})).status_code == 404


# ── disable reverses, and only what it did ──────────────────────────

async def test_disable_cancels_the_job_and_keeps_the_data(client, auth, db_session,
                                                          monkeypatch):
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    await _subscribe(db_session, user, "price_pro")
    await client.post("/api/skills/x-ray-monthly/enable", headers=auth, json={})

    resp = await client.post("/api/skills/x-ray-monthly/disable", headers=auth)
    assert resp.status_code == 200 and resp.json()["enabled"] is False

    job = (await _jobs(db_session, user.id, "instruction_run"))[0]
    await db_session.refresh(job)
    assert job.status == "cancelled"

    # the automation is switched off, NOT deleted — its history survives
    instructions = (await client.get("/api/instructions", headers=auth)).json()
    assert len(instructions) == 1 and instructions[0]["enabled"] is False


async def test_disable_restores_a_role_the_user_already_had_on(client, auth, db_session):
    """Radar was on before the skill; turning the skill off must not take it away."""
    user = await _user(db_session, client, auth)
    db_session.add(CrewConfig(user_id=user.id, role="inbox_triage", enabled=True))
    await db_session.flush()
    await db_session.commit()

    await client.post("/api/skills/inbox-triage/enable", headers=auth, json={})
    await client.post("/api/skills/inbox-triage/disable", headers=auth)

    cfg = await crew.get_config(db_session, user.id, "inbox_triage")
    await db_session.refresh(cfg)
    assert cfg.enabled is True, "disable must restore the prior state, not force off"


async def test_disable_switches_off_a_role_the_skill_turned_on(client, auth, db_session):
    user = await _user(db_session, client, auth)
    db_session.add(CrewConfig(user_id=user.id, role="inbox_triage", enabled=False))
    await db_session.flush()
    await db_session.commit()

    await client.post("/api/skills/inbox-triage/enable", headers=auth, json={})
    cfg = await crew.get_config(db_session, user.id, "inbox_triage")
    await db_session.refresh(cfg)
    assert cfg.enabled is True

    await client.post("/api/skills/inbox-triage/disable", headers=auth)
    await db_session.refresh(cfg)
    assert cfg.enabled is False


async def test_disable_does_not_cancel_the_platforms_own_sweep(client, auth, db_session,
                                                               monkeypatch):
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    await _subscribe(db_session, user, "price_pro")
    await client.get("/api/today", headers=auth)
    await client.post("/api/skills/market-watch/enable", headers=auth, json={})
    await client.post("/api/skills/market-watch/disable", headers=auth)

    job = (await _jobs(db_session, user.id, "market_radar"))[0]
    await db_session.refresh(job)
    assert job.status == "scheduled", "the built-in sweep is the platform's, not the skill's"


async def test_disable_then_enable_returns_to_a_working_state(client, auth, db_session):
    user = await _user(db_session, client, auth)
    await client.post("/api/skills/inbox-triage/enable", headers=auth, json={})
    await client.post("/api/skills/inbox-triage/disable", headers=auth)
    await client.post("/api/skills/inbox-triage/enable", headers=auth, json={})

    jobs = await _jobs(db_session, user.id, "autopilot")
    assert len([j for j in jobs if j.status == "scheduled"]) == 1


# ── gates ───────────────────────────────────────────────────────────

async def test_gated_skill_is_refused_with_the_standard_reason(client, auth, db_session,
                                                               monkeypatch):
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    await _subscribe(db_session, user, "price_basic")

    resp = await client.post("/api/skills/market-watch/enable", headers=auth, json={})
    assert resp.status_code == 402
    assert "Pro plan" in resp.json()["detail"]

    assert await crew.get_config(db_session, user.id, "radar") is None
    assert await _jobs(db_session, user.id, "market_radar") == []


async def test_listing_marks_gated_skills_with_their_reason(client, auth, db_session,
                                                            monkeypatch):
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    await _subscribe(db_session, user, "price_basic")

    skills = {s["id"]: s for s in (await client.get("/api/skills", headers=auth)).json()["skills"]}
    assert skills["market-watch"]["gated"] is True
    assert "Pro plan" in skills["market-watch"]["gate_reason"]
    assert skills["x-ray-monthly"]["gated"] is True


async def test_self_hosting_gates_nothing(client, auth):
    """No Stripe configured → every skill is available."""
    skills = (await client.get("/api/skills", headers=auth)).json()["skills"]
    assert all(s["gated"] is False for s in skills)


# ── the Manager path ────────────────────────────────────────────────

async def test_manager_tool_enables_a_skill(client, auth, db_session):
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.enable_skill",
                                    type("A", (), {"skill_id": "inbox-triage"})())
    assert out["ok"] is True and out["roles_enabled"] == ["inbox_triage"]
    assert len(await _jobs(db_session, user.id, "autopilot")) == 1


async def test_manager_tool_reports_the_gate_instead_of_bypassing_it(client, auth, db_session,
                                                                     monkeypatch):
    _hosted(monkeypatch)
    user = await _user(db_session, client, auth)
    await _subscribe(db_session, user, "price_basic")

    out = await _execute_setup_tool(db_session, user, "setup.enable_skill",
                                    type("A", (), {"skill_id": "market-watch"})())
    assert out["error"] == "gated" and "Pro plan" in out["detail"]
    assert out["feature"] == "market_radar"
    assert await _jobs(db_session, user.id, "market_radar") == []


async def test_manager_tool_rejects_an_invented_skill(client, auth, db_session):
    user = await _user(db_session, client, auth)
    out = await _execute_setup_tool(db_session, user, "setup.enable_skill",
                                    type("A", (), {"skill_id": "print-money"})())
    assert out["error"] == "unknown_skill"
    assert sorted(out["available"]) == sorted(s.id for s in catalog.CATALOG)


async def test_skill_tools_are_manager_only(client, auth, db_session):
    user = await _user(db_session, client, auth)
    out = await _execute_tool(db_session, user, "setup.enable_skill",
                              type("A", (), {"skill_id": "inbox-triage"})(), "run-1",
                              role="orchestrator")
    assert "error" in out
    assert await _jobs(db_session, user.id, "autopilot") == []


async def test_setup_state_reports_skills(client, auth, db_session):
    user = await _user(db_session, client, auth)
    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())
    assert state["skills"]["enabled"] == []
    assert sorted(state["skills"]["available"]) == sorted(s.id for s in catalog.CATALOG)

    await skills_svc.enable(db_session, user, "inbox-triage")
    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())
    assert state["skills"]["enabled"] == ["inbox-triage"]
    assert json.loads(json.dumps(state, default=str))["skills"]["enabled"] == ["inbox-triage"]


async def test_manager_can_hand_over_the_skills_page(client, auth):
    from backend.agents import actions

    await setup_mock_provider(client, auth, responses={"what can you do": json.dumps(
        {"action": "reply", "text": "Inbox Triage sorts your mail each morning. "
                                    "[See your skills](action:skills)"})})
    reply = (await client.post("/api/manager/message", headers=auth,
                               json={"text": "what can you do for me?"})).json()["reply"]
    assert actions.found_in(reply) == ["skills"]


# ── isolation ───────────────────────────────────────────────────────

async def test_skills_are_per_user(client, auth, db_session):
    from tests.conftest import auth_headers

    await client.post("/api/skills/inbox-triage/enable", headers=auth, json={})
    other = await auth_headers(client, "smuggler@example.com")
    skills = {s["id"]: s for s in (await client.get("/api/skills", headers=other)).json()["skills"]}
    assert skills["inbox-triage"]["enabled"] is False


@pytest.mark.parametrize("skill_id", [s.id for s in catalog.CATALOG])
async def test_every_skill_in_the_catalog_can_be_enabled_and_disabled(
        client, auth, db_session, skill_id):
    """Self-hosted: nothing is gated, so the whole catalog must round-trip."""
    assert (await client.post(f"/api/skills/{skill_id}/enable",
                              headers=auth, json={})).json()["enabled"] is True
    assert (await client.post(f"/api/skills/{skill_id}/disable",
                              headers=auth)).json()["enabled"] is False
