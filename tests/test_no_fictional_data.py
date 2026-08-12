"""The platform contains zero fictional data.

Demo data is not deprecated, hidden or discouraged — it cannot be created, it
cannot feed anything, and nothing derived from it is counted. What replaced it
is explanation: every surface that used to be filled with samples now describes
what the feature does and what it needs.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from backend.core.models import IntegrationConnection, User, XRayFinding, XRayRun
from backend.integrations import broker
from tests.conftest import auth_headers

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "apps" / "web"


async def _user(db_session, client, auth) -> User:
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    return await db_session.get(User, uid)


async def _seed_demo(db_session, user_id: str) -> IntegrationConnection:
    conn = IntegrationConnection(
        user_id=user_id, integration_type="demo", name="Demo data (synthetic)",
        capabilities_json={"gmail.read": "READ", "calendar.read": "READ"})
    db_session.add(conn)
    await db_session.flush()
    await db_session.commit()
    return conn


# ── 1. no route can create demo data ────────────────────────────────

async def test_the_demo_integration_cannot_be_created(client, auth):
    resp = await client.post("/api/integrations", headers=auth, json={
        "integration_type": "demo", "name": "Demo data (synthetic)",
        "capabilities": {"gmail.read": "READ"}})
    assert resp.status_code == 410
    detail = resp.json()["detail"]
    assert "removed" in detail.lower() and "simulated" in detail.lower()

    assert (await client.get("/api/integrations", headers=auth)).json() == []


async def test_every_synthetic_type_is_refused(client, auth):
    assert broker.SYNTHETIC_TYPES, "the guard list must not be empty"
    for kind in broker.SYNTHETIC_TYPES:
        resp = await client.post("/api/integrations", headers=auth, json={
            "integration_type": kind, "name": "x"})
        assert resp.status_code == 410, kind


def test_no_ui_offers_to_create_demo_data():
    for page in ("app/connections/page.tsx", "app/data/page.tsx", "app/command/page.tsx"):
        source = (WEB / page).read_text(encoding="utf-8")
        assert 'integration_type: "demo"' not in source, page
        assert "Add demo data" not in source, page
        assert "synthetic demo data" not in source, page


def test_the_landing_page_shows_no_invented_numbers():
    page = (WEB / "app" / "page.tsx").read_text(encoding="utf-8")
    assert "Demo data shown as an example" not in page
    assert "Example SaaS" not in page
    assert "No sample data anywhere in Moseisley" in page


# ── 2. existing demo data feeds nothing ─────────────────────────────

async def test_a_demo_connection_is_never_discovered_as_a_data_source(db_session, client,
                                                                      auth, monkeypatch):
    """Production excludes synthetic sources outright; only the test env reads them."""
    user = await _user(db_session, client, auth)
    await _seed_demo(db_session, user.id)

    monkeypatch.setattr(broker, "_synthetic_allowed", lambda: False)
    found = await broker.find_connection_for_capability(db_session, user.id, "gmail.read")
    assert found is None, "invented data must never be picked up as a source"


async def test_findings_from_the_demo_dataset_stop_counting(db_session, client, auth):
    user = await _user(db_session, client, auth)
    run = XRayRun(user_id=user.id, horizon_days=90, status="completed",
                  summary_json={"demo_data": True, "findings": 1})
    db_session.add(run)
    await db_session.flush()
    db_session.add(XRayFinding(
        user_id=user.id, run_id=run.id, type="found_money", title="Invoice #2041",
        description="from the retired demo dataset", confidence=0.9, verified=True,
        estimated_value_cents=240_000, status="open"))
    await db_session.flush()
    await db_session.commit()

    today = (await client.get("/api/today", headers=auth)).json()
    assert today["value_found_this_month"]["verified_money_cents"] == 0

    latest = (await client.get("/api/xray/latest", headers=auth)).json()
    assert latest["run"] is None and latest["findings"] == {}

    runs = (await client.get("/api/xray/runs", headers=auth)).json()
    assert runs == []


async def test_a_real_run_is_still_shown_alongside_a_demo_one(db_session, client, auth):
    """The exclusion must be surgical: only the invented run disappears."""
    user = await _user(db_session, client, auth)
    for summary in ({"demo_data": True}, {"findings": 1}):
        run = XRayRun(user_id=user.id, horizon_days=90, status="completed",
                      summary_json=summary)
        db_session.add(run)
        await db_session.flush()
        db_session.add(XRayFinding(
            user_id=user.id, run_id=run.id, type="found_money",
            title="real" if "findings" in summary else "demo", description="",
            confidence=0.9, verified=True, estimated_value_cents=1000, status="open"))
    await db_session.flush()
    await db_session.commit()

    latest = (await client.get("/api/xray/latest", headers=auth)).json()
    assert latest["run"] is not None
    titles = [f["title"] for group in latest["findings"].values() for f in group]
    assert titles == ["real"]
    assert (await client.get("/api/today", headers=auth)).json(
        )["value_found_this_month"]["verified_money_cents"] == 1000


# ── 3. the removal notice, wired to the existing clear path ─────────

async def test_a_user_with_demo_data_can_still_clear_it(db_session, client, auth):
    user = await _user(db_session, client, auth)
    await _seed_demo(db_session, user.id)
    assert len((await client.get("/api/integrations", headers=auth)).json()) == 1

    resp = await client.post("/api/integrations/demo/clear", headers=auth)
    assert resp.status_code == 200
    assert (await client.get("/api/integrations", headers=auth)).json() == []


async def test_clearing_removes_the_findings_the_demo_data_produced(db_session, client, auth):
    user = await _user(db_session, client, auth)
    await _seed_demo(db_session, user.id)
    run = XRayRun(user_id=user.id, horizon_days=90, status="completed",
                  summary_json={"demo_data": True})
    db_session.add(run)
    await db_session.flush()
    db_session.add(XRayFinding(user_id=user.id, run_id=run.id, type="found_money",
                               title="demo", description="", confidence=0.9,
                               verified=True, status="open"))
    await db_session.flush()
    await db_session.commit()

    await client.post("/api/integrations/demo/clear", headers=auth)
    left = (await db_session.execute(select(XRayFinding).where(
        XRayFinding.user_id == user.id))).scalars().all()
    assert left == []


async def test_a_user_without_demo_data_gets_nothing_to_clear(client, auth):
    assert (await client.post("/api/integrations/demo/clear", headers=auth)).status_code == 404


def test_the_banner_announces_the_removal_and_offers_the_clear():
    banner = (WEB / "components" / "demo-banner.tsx").read_text(encoding="utf-8")
    assert "demo data removed" in banner
    assert "Clear mine now" in banner
    assert "/integrations/demo/clear" in banner
    # it no longer sells demo data as a way to see the product
    assert "sample findings so you can see" not in banner


def test_findings_are_no_longer_labelled_as_demo_because_none_are_shown():
    page = (WEB / "app" / "xray" / "page.tsx").read_text(encoding="utf-8")
    assert "DemoPill" not in page


# ── 4. explanation replaced simulation ──────────────────────────────

def test_xray_explains_what_it_finds_instead_of_showing_a_sample():
    page = (WEB / "app" / "xray" / "page.tsx").read_text(encoding="utf-8")
    assert "XRayExplainer" in page and "LOOKS_FOR" in page
    for claim in ("Unpaid and overdue invoices", "Warm leads that went cold",
                  "Promises you did not keep", "Recoverable hours"):
        assert claim in page, claim
    assert "this is a description, not a preview" in page
    assert "Connect a source" in page


def test_the_command_center_explains_its_empty_metrics():
    page = (WEB / "app" / "command" / "page.tsx").read_text(encoding="utf-8")
    assert "no numbers yet" in page
    assert "an empty number means it has not happened yet" in page
    assert "A sweep reads public sources" in page          # radar
    assert "there are no examples to show you first" in page   # x-ray card


def test_no_skill_offers_demo_data_as_a_way_to_satisfy_it():
    from backend.skills import catalog

    for skill in catalog.CATALOG:
        blob = " ".join(skill.requirements + skill.what_it_does).lower()
        assert "demo data" not in blob, skill.id
        assert "synthetic" not in blob, skill.id


def test_the_manager_is_told_the_platform_has_no_demo_data():
    from backend.agents import crew

    reference = crew.platform_reference()
    assert "THERE IS NO DEMO DATA" in reference
    assert "Never offer to generate sample data" in reference
    assert "synthetic demo data" not in reference


async def test_setup_state_still_reports_a_legacy_demo_connection(db_session, client, auth):
    """The Manager needs to know, so it can tell the user to clear it."""
    from backend.agents.orchestrator import EmptyArgs, _execute_setup_tool

    user = await _user(db_session, client, auth)
    await _seed_demo(db_session, user.id)
    state = await _execute_setup_tool(db_session, user, "setup.state", EmptyArgs())
    assert state["integrations"]["demo"] is True


async def test_a_second_user_is_unaffected_by_another_users_legacy_demo(client, auth,
                                                                        db_session):
    user = await _user(db_session, client, auth)
    await _seed_demo(db_session, user.id)
    other = await auth_headers(client, "clean@example.com")
    assert (await client.get("/api/integrations", headers=other)).json() == []
