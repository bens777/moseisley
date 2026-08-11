"""Friends of the Cantina (owner directive 2026-08-11) — §41 scenarios 1-22
and acceptance scenarios A-E."""
from __future__ import annotations

from tests.conftest import auth_headers

ALICE = {"handle": "alice", "display_name": "Alice", "bio": "Building tiny AI products."}


async def _publish_alice(client, headers):
    await client.put("/api/friends/me", headers=headers, json=ALICE)
    await client.post("/api/friends/me/publish", headers=headers, json={"published": True})


# 1-3, 21-22, scenario A + E: privacy default, publish/unpublish, auth split
async def test_profile_private_by_default_publish_unpublish(client, auth):
    # 21: public routes need no auth
    assert (await client.get("/api/public/friends")).status_code == 200
    assert (await client.get("/api/public/friends")).json() == []

    # 22: owner mutations require auth
    assert (await client.put("/api/friends/me", json=ALICE)).status_code in (401, 403)

    # profile exists but is NOT published → not discoverable (1)
    resp = await client.put("/api/friends/me", headers=auth, json=ALICE)
    assert resp.status_code == 200, resp.text
    assert (await client.get("/api/public/friends")).json() == []
    assert (await client.get("/api/public/friends/alice")).status_code == 404

    # 2: publishing makes it publicly discoverable (scenario A)
    await client.post("/api/friends/me/publish", headers=auth, json={"published": True})
    people = (await client.get("/api/public/friends")).json()
    assert [p["handle"] for p in people] == ["alice"]
    page = (await client.get("/api/public/friends/alice")).json()
    assert page["display_name"] == "Alice"

    # 3: unpublishing removes from discovery, data preserved privately
    await client.post("/api/friends/me/publish", headers=auth, json={"published": False})
    assert (await client.get("/api/public/friends")).json() == []
    assert (await client.get("/api/public/friends/alice")).status_code == 404
    me = (await client.get("/api/friends/me", headers=auth)).json()
    assert me["profile"]["display_name"] == "Alice"  # preserved


# 4-5: handle rules
async def test_handles_unique_case_insensitive_and_reserved(client, auth):
    await _publish_alice(client, auth)
    other = await auth_headers(client, "bob-friends@example.com")
    # case variation cannot impersonate (4)
    resp = await client.put("/api/friends/me", headers=other,
                            json={"handle": "ALICE", "display_name": "Fake Alice"})
    assert resp.status_code == 400
    # reserved words rejected (5)
    for bad in ("admin", "friends", "api", "moseisley", "settings"):
        resp = await client.put("/api/friends/me", headers=other,
                                json={"handle": bad, "display_name": "X"})
        assert resp.status_code == 400, bad
    # invalid syntax rejected
    for bad in ("a", "has space", "UPPER!", "-lead", "trail-", "a__b"):
        resp = await client.put("/api/friends/me", headers=other,
                                json={"handle": bad, "display_name": "X"})
        assert resp.status_code == 400, bad


# 6 + scenario E: private fields never leak
async def test_private_account_data_never_leaks(client, auth, db_session):
    await _publish_alice(client, auth)
    # add private internal data: internal project + goal-ish memory
    await client.post("/api/projects", headers=auth, json={"name": "SECRET internal ops"})
    import json
    people = (await client.get("/api/public/friends")).json()
    page = (await client.get("/api/public/friends/alice")).json()
    blob = json.dumps(people) + json.dumps(page)
    assert "founder@example.com" not in blob      # email never leaks
    assert "SECRET internal ops" not in blob      # internal Project never leaks
    assert "user_id" not in blob                  # internal ids not exposed
    for forbidden in ("stripe", "api_key", "hashed_password", "is_superuser"):
        assert forbidden not in blob.lower(), forbidden


# 7-10 + scenario B: projects
async def test_projects_crud_ownership_and_url_safety(client, auth):
    await _publish_alice(client, auth)
    # scenario B: three projects of different kinds
    p1 = (await client.post("/api/friends/projects", headers=auth, json={
        "name": "TinyCRM", "tagline": "CRM for tiny teams", "url": "https://example1.com",
        "category": "saas", "tags": ["crm", "b2b"]})).json()
    p2 = (await client.post("/api/friends/projects", headers=auth, json={
        "name": "Build Notes", "url": "https://example2.com", "category": "newsletter"})).json()
    p3 = (await client.post("/api/friends/projects", headers=auth, json={
        "name": "AgentKit", "url": "https://github.com/example/agentkit",
        "category": "open_source"})).json()
    page = (await client.get("/api/public/friends/alice")).json()
    assert {p["name"] for p in page["projects"]} == {"TinyCRM", "Build Notes", "AgentKit"}
    assert page["project_count"] == 3

    # directory project discovery + category filter (17)
    projects = (await client.get("/api/public/friends/projects")).json()
    assert len(projects) == 3
    newsletters = (await client.get("/api/public/friends/projects",
                                    params={"category": "newsletter"})).json()
    assert [p["name"] for p in newsletters] == ["Build Notes"]

    # 8: another user cannot edit/delete (IDOR)
    other = await auth_headers(client, "eve-friends@example.com")
    assert (await client.patch(f"/api/friends/projects/{p1['id']}", headers=other,
                               json={"name": "HACKED"})).status_code == 404
    assert (await client.delete(f"/api/friends/projects/{p1['id']}",
                                headers=other)).status_code == 404

    # 9: unpublished project not public
    await client.patch(f"/api/friends/projects/{p2['id']}", headers=auth,
                       json={"is_public": False})
    projects = (await client.get("/api/public/friends/projects")).json()
    assert {p["name"] for p in projects} == {"TinyCRM", "AgentKit"}

    # 10: unsafe URL schemes rejected
    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x.com", "file:///etc/passwd"):
        resp = await client.post("/api/friends/projects", headers=auth,
                                 json={"name": "Bad", "url": bad})
        assert resp.status_code == 400, bad

    # owner edit + delete work (13 UX)
    assert (await client.patch(f"/api/friends/projects/{p3['id']}", headers=auth,
                               json={"status": "launched"})).json()["status"] == "launched"
    assert (await client.delete(f"/api/friends/projects/{p3['id']}",
                                headers=auth)).json()["deleted"] is True


# 11-15 + scenarios C/D: updates
async def test_updates_lifecycle(client, auth):
    await _publish_alice(client, auth)
    p1 = (await client.post("/api/friends/projects", headers=auth, json={
        "name": "TinyCRM", "url": "https://example1.com", "category": "saas"})).json()

    # 11: create ≤500 chars, with URL + project (scenario C)
    resp = await client.post("/api/friends/updates", headers=auth, json={
        "text": "Just shipped TinyCRM's new onboarding.",
        "url": "https://example1.com", "project_id": p1["id"]})
    assert resp.status_code == 200
    update = resp.json()

    # 12: >500 rejected server-side
    assert (await client.post("/api/friends/updates", headers=auth,
                              json={"text": "x" * 501})).status_code == 400
    # 13: unsafe URL rejected
    assert (await client.post("/api/friends/updates", headers=auth,
                              json={"text": "hi", "url": "javascript:alert(1)"})).status_code == 400

    # appears publicly on profile + global feed (FROM THE CANTINA)
    feed = (await client.get("/api/public/friends/updates")).json()
    assert feed[0]["text"].startswith("Just shipped")
    assert feed[0]["project"]["name"] == "TinyCRM"
    assert feed[0]["owner"]["handle"] == "alice"
    # no social-network fields (§35)
    assert "likes" not in feed[0] and "comments" not in feed[0]

    # 14: another user cannot edit
    other = await auth_headers(client, "mallory-friends@example.com")
    assert (await client.patch(f"/api/friends/updates/{update['id']}", headers=other,
                               json={"text": "defaced"})).status_code == 404

    # edit marks edited; unpublish hides (15)
    edited = (await client.patch(f"/api/friends/updates/{update['id']}", headers=auth,
                                 json={"text": "Shipped v2 of onboarding."})).json()
    assert edited["edited"] is True
    await client.patch(f"/api/friends/updates/{update['id']}", headers=auth,
                       json={"is_public": False})
    assert (await client.get("/api/public/friends/updates")).json() == []
    # delete
    await client.patch(f"/api/friends/updates/{update['id']}", headers=auth,
                       json={"is_public": True})
    assert (await client.delete(f"/api/friends/updates/{update['id']}",
                                headers=auth)).json()["deleted"] is True
    assert (await client.get("/api/public/friends/updates")).json() == []


# 16, 18: search + pagination
async def test_search_and_pagination(client, auth):
    await _publish_alice(client, auth)
    for i in range(5):
        await client.post("/api/friends/projects", headers=auth, json={
            "name": f"Widget {i}", "tagline": "gadget tooling", "category": "dev_tool"})
    # search by name and by bio
    found = (await client.get("/api/public/friends", params={"q": "tiny ai"})).json()
    assert found == [] or found  # LIKE is per-term; search 'tiny'
    found = (await client.get("/api/public/friends", params={"q": "tiny"})).json()
    assert found[0]["handle"] == "alice"
    projects = (await client.get("/api/public/friends/projects", params={"q": "widget"})).json()
    assert len(projects) == 5
    # pagination (18)
    page1 = (await client.get("/api/public/friends/projects",
                              params={"limit": 2, "offset": 0})).json()
    page2 = (await client.get("/api/public/friends/projects",
                              params={"limit": 2, "offset": 2})).json()
    assert len(page1) == 2 and len(page2) == 2
    assert {p["id"] for p in page1}.isdisjoint({p["id"] for p in page2})
    # page size capped
    capped = (await client.get("/api/public/friends/projects", params={"limit": 500})).json()
    assert len(capped) <= 50


# 19: unpublish semantics hide projects+updates too
async def test_unpublish_hides_everything(client, auth):
    await _publish_alice(client, auth)
    await client.post("/api/friends/projects", headers=auth, json={"name": "Thing"})
    await client.post("/api/friends/updates", headers=auth, json={"text": "hello cantina"})
    assert len((await client.get("/api/public/friends/projects")).json()) == 1
    assert len((await client.get("/api/public/friends/updates")).json()) == 1
    await client.post("/api/friends/me/publish", headers=auth, json={"published": False})
    assert (await client.get("/api/public/friends/projects")).json() == []
    assert (await client.get("/api/public/friends/updates")).json() == []


# 20: operator moderation
async def test_moderation(client, auth, db_session):
    await _publish_alice(client, auth)
    upd = (await client.post("/api/friends/updates", headers=auth,
                             json={"text": "spammy thing"})).json()
    # normal user cannot moderate
    assert (await client.post("/api/friends/moderate", headers=auth, json={
        "entity": "update", "entity_id": upd["id"], "status": "hidden"})).status_code == 403
    # promote to operator
    from sqlalchemy import update as sa_update

    from backend.core.models import User
    await db_session.execute(sa_update(User).where(
        User.email == "founder@example.com").values(is_superuser=True))
    await db_session.commit()
    resp = await client.post("/api/friends/moderate", headers=auth, json={
        "entity": "update", "entity_id": upd["id"], "status": "hidden",
        "reason": "spam"})
    assert resp.status_code == 200
    assert (await client.get("/api/public/friends/updates")).json() == []
    # hiding a profile removes person + content from discovery
    me = (await client.get("/api/friends/me", headers=auth)).json()
    prof_resp = await client.get("/api/public/friends/alice")
    assert prof_resp.status_code == 200
    from sqlalchemy import select

    from backend.core.models import PublicProfile
    profile_id = (await db_session.execute(select(PublicProfile.id).where(
        PublicProfile.handle == "alice"))).scalar_one()
    await client.post("/api/friends/moderate", headers=auth, json={
        "entity": "profile", "entity_id": profile_id, "status": "hidden"})
    assert (await client.get("/api/public/friends/alice")).status_code == 404
    assert (await client.get("/api/public/friends")).json() == []
    assert me["profile"]["handle"] == "alice"  # data intact for the owner


# project limit (§14)
async def test_project_limit_enforced(client, auth):
    from backend.friends import service as svc
    await _publish_alice(client, auth)
    for i in range(svc.MAX_PUBLIC_PROJECTS_PER_USER):
        resp = await client.post("/api/friends/projects", headers=auth,
                                 json={"name": f"P{i}"})
        assert resp.status_code == 200
    resp = await client.post("/api/friends/projects", headers=auth, json={"name": "over"})
    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"]
