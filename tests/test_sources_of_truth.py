"""The two source-of-truth surfaces: SCHEDULE (every cron) and MY DATA.

Both are read-mostly views over records that already existed — so what matters
is that they tell the truth, that their two edits write through the owning
service, and that a switched-off job stays off.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.agents.orchestrator import EmptyArgs, _execute_setup_tool, _execute_tool
from backend.core.models import ScheduledJob, User
from backend.jobs import user_schedule
from tests.conftest import auth_headers, setup_mock_provider


async def _user(db_session, client, auth) -> User:
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    return await db_session.get(User, uid)


async def _job(db_session, user_id: str, job_type: str) -> ScheduledJob:
    return (await db_session.execute(select(ScheduledJob).where(
        ScheduledJob.user_id == user_id,
        ScheduledJob.job_type == job_type))).scalars().first()


# ── SCHEDULE: listing accuracy ──────────────────────────────────────

async def test_schedule_lists_the_default_jobs_in_human_terms(client, auth, db_session):
    """/today seeds the built-in schedules; the page must explain them."""
    await client.get("/api/today", headers=auth)

    body = (await client.get("/api/schedule", headers=auth)).json()
    assert body["timezone"] == "UTC"
    by_type = {j["job_type"]: j for j in body["jobs"]}
    assert set(by_type) == {"market_radar", "daily_strategist", "weekly_review"}

    radar = by_type["market_radar"]
    assert radar["title"] == "Radar sweep" and radar["role"] == "radar"
    assert radar["what"]                       # a human sentence, not a job type
    assert radar["cadence"] == "every day at 06:00 (UTC)"
    assert radar["enabled"] is True and radar["next_run_at"] is not None
    assert radar["last_result"] == {"status": "never_run", "detail": None}
    assert radar["editable"] is True

    assert by_type["weekly_review"]["cadence"].startswith("every Monday at 08:30")
    assert by_type["daily_strategist"]["role"] == "strategist"


async def test_schedule_reports_the_last_result_truthfully(client, auth, db_session):
    await client.get("/api/today", headers=auth)
    user = await _user(db_session, client, auth)

    job = await _job(db_session, user.id, "market_radar")
    job.last_run_at = datetime.now(UTC) - timedelta(hours=2)
    job.payload_json = {"last_result": {"skipped": "scheduled autonomy requires the Pro plan"}}
    await db_session.flush()
    await db_session.commit()
    row = next(j for j in (await client.get("/api/schedule", headers=auth)).json()["jobs"]
               if j["job_type"] == "market_radar")
    assert row["last_result"]["status"] == "skipped"
    assert "Pro plan" in row["last_result"]["detail"]

    job = await _job(db_session, user.id, "market_radar")
    job.last_error = "RuntimeError: provider unreachable"
    await db_session.flush()
    await db_session.commit()
    row = next(j for j in (await client.get("/api/schedule", headers=auth)).json()["jobs"]
               if j["job_type"] == "market_radar")
    assert row["last_result"] == {"status": "error",
                                  "detail": "RuntimeError: provider unreachable"}


async def test_saved_automations_appear_with_their_own_name(client, auth):
    await client.post("/api/instructions", headers=auth, json={
        "name": "Watch the cantina market", "kind": "market_watch",
        "config": {"topics": ["OpenClaw"], "instruction": "Only material changes."},
        "schedule": {"frequency": "daily", "time": "07:30", "timezone": "UTC"},
        "assigned_role": "radar",
    })
    jobs = (await client.get("/api/schedule", headers=auth)).json()["jobs"]
    watch = next(j for j in jobs if j["job_type"] == "instruction_run")
    assert watch["title"] == "Watch the cantina market"
    assert watch["role"] == "radar"
    assert watch["what"] == "Only material changes."
    assert watch["cadence"] == "every day at 07:30 (UTC)"
    assert watch["instruction_id"]


# ── SCHEDULE: the toggle ────────────────────────────────────────────

async def test_toggling_a_built_in_job_off_keeps_it_off(client, auth, db_session):
    """The Command Center re-seeds defaults on every load — a job the user
    switched off must not quietly come back."""
    await client.get("/api/today", headers=auth)
    user = await _user(db_session, client, auth)
    radar = await _job(db_session, user.id, "market_radar")

    off = (await client.post(f"/api/schedule/{radar.id}/toggle", headers=auth,
                             json={"enabled": False})).json()
    assert off["enabled"] is False and off["next_run_at"] is None

    await client.get("/api/today", headers=auth)          # the re-seeding path
    jobs = (await client.get("/api/schedule", headers=auth)).json()["jobs"]
    radar_rows = [j for j in jobs if j["job_type"] == "market_radar"]
    assert len(radar_rows) == 1 and radar_rows[0]["enabled"] is False

    on = (await client.post(f"/api/schedule/{radar_rows[0]['id']}/toggle", headers=auth,
                            json={"enabled": True})).json()
    assert on["enabled"] is True and on["next_run_at"] is not None
    user = await _user(db_session, client, auth)
    await db_session.refresh(user)
    assert user_schedule.disabled_types(user) == []


async def test_disabled_job_is_not_claimed_by_the_worker(client, auth, db_session):
    await client.get("/api/today", headers=auth)
    user = await _user(db_session, client, auth)
    job = await _job(db_session, user.id, "daily_strategist")
    job.next_run_at = datetime.now(UTC) - timedelta(minutes=1)   # due right now
    await db_session.flush()
    await db_session.commit()

    await client.post(f"/api/schedule/{job.id}/toggle", headers=auth, json={"enabled": False})
    refreshed = await _job(db_session, user.id, "daily_strategist")
    await db_session.refresh(refreshed)
    assert refreshed.status == "cancelled"      # claim_one only takes "scheduled"


async def test_toggling_an_automation_writes_through_its_instruction(client, auth):
    created = (await client.post("/api/instructions", headers=auth, json={
        "name": "Weekly goal review", "kind": "goal_review", "config": {},
        "schedule": {"frequency": "weekly", "time": "09:00", "timezone": "UTC"},
    })).json()
    job = next(j for j in (await client.get("/api/schedule", headers=auth)).json()["jobs"]
               if j["instruction_id"] == created["id"])

    await client.post(f"/api/schedule/{job['id']}/toggle", headers=auth, json={"enabled": False})
    # the owning record moved too — the two can never disagree
    assert (await client.get(f"/api/instructions/{created['id']}",
                             headers=auth)).json()["enabled"] is False


# ── SCHEDULE: cadence editing ───────────────────────────────────────

async def test_cadence_preset_moves_a_built_in_job(client, auth, db_session):
    await client.get("/api/today", headers=auth)
    user = await _user(db_session, client, auth)
    radar = await _job(db_session, user.id, "market_radar")

    updated = (await client.put(f"/api/schedule/{radar.id}/cadence", headers=auth,
                                json={"frequency": "weekly", "time": "17:15",
                                      "weekday": 2})).json()
    assert updated["cadence"] == "every Wednesday at 17:15 (UTC)"
    assert updated["interval_seconds"] == 7 * 86400

    hourly = (await client.put(f"/api/schedule/{radar.id}/cadence", headers=auth,
                               json={"frequency": "hourly"})).json()
    assert hourly["cadence"] == "every hour" and hourly["interval_seconds"] == 3600


async def test_cadence_edit_on_an_automation_updates_the_instruction(client, auth):
    created = (await client.post("/api/instructions", headers=auth, json={
        "name": "Daily digest", "kind": "goal_review", "config": {},
        "schedule": {"frequency": "daily", "time": "08:00", "timezone": "UTC"},
    })).json()
    job = next(j for j in (await client.get("/api/schedule", headers=auth)).json()["jobs"]
               if j["instruction_id"] == created["id"])

    updated = (await client.put(f"/api/schedule/{job['id']}/cadence", headers=auth,
                                json={"frequency": "daily", "time": "06:45"})).json()
    assert updated["cadence"] == "every day at 06:45 (UTC)"
    instruction = (await client.get(f"/api/instructions/{created['id']}", headers=auth)).json()
    assert instruction["schedule"]["time"] == "06:45"


async def test_cadence_rejects_anything_outside_the_presets(client, auth, db_session):
    await client.get("/api/today", headers=auth)
    user = await _user(db_session, client, auth)
    radar = await _job(db_session, user.id, "market_radar")
    for bad in ({"frequency": "every-full-moon"}, {"frequency": "daily", "time": "25:00"},
                {"frequency": "weekly", "time": "08:00", "weekday": 9}):
        resp = await client.put(f"/api/schedule/{radar.id}/cadence", headers=auth, json=bad)
        assert resp.status_code in (400, 422), bad


async def test_schedule_is_per_user(client, auth):
    await client.get("/api/today", headers=auth)
    other = await auth_headers(client, "smuggler@example.com")
    assert (await client.get("/api/schedule", headers=other)).json()["jobs"] == []


# ── MY DATA: documents ──────────────────────────────────────────────

async def test_pasted_knowledge_is_stored_listed_and_deletable(client, auth):
    saved = (await client.put("/api/documents", headers=auth, json={
        "path": "/knowledge/pricing-rules.md",
        "content_md": "# Pricing\n\nNever discount below 20%.",
        "metadata": {"title": "Pricing rules", "source": "my-data"},
    })).json()
    assert saved["path"] == "/knowledge/pricing-rules.md"
    assert saved["created_at"]

    docs = (await client.get("/api/documents", headers=auth)).json()
    knowledge = [d for d in docs if d["path"].startswith("/knowledge/")]
    assert [d["metadata"]["title"] for d in knowledge] == ["Pricing rules"]

    resp = await client.delete(f"/api/documents/{saved['id']}", headers=auth)
    assert resp.json() == {"deleted": True, "path": "/knowledge/pricing-rules.md"}
    docs = (await client.get("/api/documents", headers=auth)).json()
    assert not [d for d in docs if d["path"].startswith("/knowledge/")]


async def test_structural_context_documents_cannot_be_deleted(client, auth):
    docs = (await client.get("/api/documents", headers=auth)).json()   # seeds defaults
    constitution = next(d for d in docs if d["path"] == "/context/constitution.md")
    resp = await client.delete(f"/api/documents/{constitution['id']}", headers=auth)
    assert resp.status_code == 400
    assert "built-in" in resp.json()["detail"]
    assert (await client.get("/api/documents/by-path", headers=auth,
                             params={"path": "/context/constitution.md"})).status_code == 200


async def test_one_user_cannot_delete_another_users_document(client, auth):
    mine = (await client.put("/api/documents", headers=auth, json={
        "path": "/knowledge/private.md", "content_md": "secret"})).json()
    other = await auth_headers(client, "smuggler@example.com")
    assert (await client.delete(f"/api/documents/{mine['id']}",
                                headers=other)).status_code == 404


async def test_the_crew_can_read_what_the_user_pasted(client, auth, db_session):
    """Storing it is only worth anything if an agent can retrieve it."""
    await client.put("/api/documents", headers=auth, json={
        "path": "/knowledge/clients.md",
        "content_md": "Our biggest client is Jabba Logistics, renewing in March."})
    user = await _user(db_session, client, auth)

    out = await _execute_tool(db_session, user, "knowledge.search",
                              type("A", (), {"query": "Jabba"})(), "run-1")
    assert [d["name"] for d in out["documents"]] == ["clients.md"]
    assert "Jabba Logistics" in out["documents"][0]["excerpt"]


# ── Manager awareness of both surfaces ──────────────────────────────

async def test_setup_state_counts_schedules_and_documents(client, auth, db_session):
    await client.get("/api/today", headers=auth)
    await client.put("/api/documents", headers=auth, json={
        "path": "/knowledge/about-me.md", "content_md": "I run a cantina."})
    await client.put("/api/documents", headers=auth, json={
        "path": "/knowledge/offer.md", "content_md": "Drinks and repairs."})
    user = await _user(db_session, client, auth)

    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())
    assert state["documents"]["count"] == 2
    assert sorted(state["documents"]["titles"]) == ["about-me.md", "offer.md"]
    # the context documents the platform seeds are not "what the user gave us"
    assert state["schedules"]["count"] == 3
    radar = next(j for j in state["schedules"]["jobs"] if j["job_type"] == "market_radar")
    assert radar["role"] == "radar"
    assert radar["cadence"] == "every day at 06:00 (UTC)"
    assert json.loads(json.dumps(state, default=str))["documents"]["count"] == 2


async def test_manager_can_hand_over_both_new_pages(client, auth):
    from backend.agents import actions

    assert actions.ACTION_ROUTES["schedule"] == "/schedule"
    assert actions.ACTION_ROUTES["data"] == "/data"
    await setup_mock_provider(client, auth, responses={
        "what do you know": json.dumps({"action": "reply", "text":
            "You have 2 documents and your Radar runs every morning — "
            "[see your schedule](action:schedule) or [review your data](action:data)."}),
    })
    reply = (await client.post("/api/manager/message", headers=auth,
                               json={"text": "what do you know about me?"})).json()["reply"]
    assert actions.found_in(reply) == ["schedule", "data"]


@pytest.mark.parametrize("job_type,expected", [
    ("market_radar", "radar"), ("daily_strategist", "strategist"),
    ("weekly_review", "auditor"), ("instruction_run", "instruction"),
])
def test_every_job_type_has_a_human_owner(job_type, expected):
    assert user_schedule.role_for(job_type) == expected
    title, _role, what = user_schedule.JOB_CATALOG[job_type]
    assert title and what
