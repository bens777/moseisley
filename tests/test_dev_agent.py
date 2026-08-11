"""Third pass §62: Dev Agent — review → proposal → isolated patch → tests →
hash-bound approval; main untouched; approvals invalidated on patch change."""
from __future__ import annotations

import json

import pytest

from backend.agents import dev as dev_svc
from backend.core.config import get_settings
from tests.conftest import setup_mock_provider

REVIEW_JSON = json.dumps({"proposals": [{
    "title": "Add dev agent test artifact",
    "why": "The platform lacks a marker file used by integration checks.",
    "expected_benefit": "Faster verification.",
    "evidence": ["telemetry: 0 failed runs"],
    "plan_md": "Create docs/DEV_AGENT_TEST.md with one line.",
    "files_affected": ["docs/DEV_AGENT_TEST.md"],
    "schema_impact": "none",
    "risk": "low",
    "test_plan": "Existing suite must stay green.",
}]})

PATCH_DIFF = (
    "diff --git a/docs/DEV_AGENT_TEST.md b/docs/DEV_AGENT_TEST.md\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/docs/DEV_AGENT_TEST.md\n"
    "@@ -0,0 +1 @@\n"
    "+dev agent test artifact\n"
)
PATCH_JSON = json.dumps({"patch": PATCH_DIFF, "notes": "adds marker file"})

WEEKLY_REVIEW = {
    "name": "Weekly platform review", "kind": "dev_review",
    "config": {"instruction": "Standard weekly review."},
    "schedule": {"frequency": "weekly", "time": "09:00", "weekday": 4,
                 "timezone": "Europe/Paris"},
    "assigned_role": "dev",
}


@pytest.fixture
async def _cleanup_worktrees():
    yield
    import subprocess
    repo = str(dev_svc.REPO_ROOT)
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("worktree ") and "dev-worktrees" in line:
            subprocess.run(["git", "worktree", "remove", "--force",
                            line.split(" ", 1)[1]], cwd=repo, capture_output=True)
    branches = subprocess.run(["git", "branch", "--list", "dev/proposal-*"],
                              cwd=repo, capture_output=True, text=True).stdout.split()
    for b in branches:
        if b.startswith("dev/"):
            subprocess.run(["git", "branch", "-D", b], cwd=repo, capture_output=True)


async def test_dev_review_creates_proposals(client, auth):
    """Weekly review is user-configured (no hardcoded schedule) and persists
    evidence-backed proposals."""
    await setup_mock_provider(client, auth, responses={"Standard weekly review": REVIEW_JSON})
    ins = (await client.post("/api/instructions", headers=auth, json=WEEKLY_REVIEW)).json()
    assert ins["schedule"]["weekday"] == 4  # user-chosen day preserved

    result = (await client.post(f"/api/instructions/{ins['id']}/run", headers=auth)).json()
    assert result["proposals_created"] == 1

    proposals = (await client.get("/api/dev/proposals", headers=auth)).json()
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == "proposed" and p["risk"] == "low"
    assert p["files_affected"] == ["docs/DEV_AGENT_TEST.md"]

    acts = (await client.get("/api/activity", headers=auth)).json()
    assert "dev_proposal_created" in {e["event_type"] for e in acts}


async def test_dev_patch_isolated_and_hash_bound(client, auth, db_session, monkeypatch,
                                                 _cleanup_worktrees):
    """§62: patch prepared in an isolated worktree; main unchanged; approval
    bound to patch hash; changed patch invalidates approval."""
    monkeypatch.setattr(get_settings(), "dev_test_command", "true")
    await setup_mock_provider(client, auth, responses={
        "Standard weekly review": REVIEW_JSON,
        "preparing a patch": PATCH_JSON,
    })
    ins = (await client.post("/api/instructions", headers=auth, json=WEEKLY_REVIEW)).json()
    await client.post(f"/api/instructions/{ins['id']}/run", headers=auth)
    proposal = (await client.get("/api/dev/proposals", headers=auth)).json()[0]

    resp = await client.post(f"/api/dev/proposals/{proposal['id']}/prepare-patch",
                             headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "patch_ready" and body["tests_passed"] is True
    patch_hash = body["patch_hash"]

    # main is untouched: the marker file does NOT exist in the real repo tree
    assert not (dev_svc.REPO_ROOT / "docs" / "DEV_AGENT_TEST.md").exists()

    detail = (await client.get(f"/api/dev/proposals/{proposal['id']}", headers=auth)).json()
    assert detail["approval_id"] is not None
    assert detail["patch_hash"] == patch_hash
    assert detail["test_results"]["exit_code"] == 0

    # approve via the dashboard → bound to the exact hash
    resp = await client.post(f"/api/dev/proposals/{proposal['id']}/resolve", headers=auth)
    assert resp.json()["status"] == "approved"
    detail = (await client.get(f"/api/dev/proposals/{proposal['id']}", headers=auth)).json()
    assert detail["approved_patch_hash"] == patch_hash

    # simulate the patch changing after approval → merge must refuse + invalidate
    from sqlalchemy import select

    from backend.core.models import DevProposal
    row = (await db_session.execute(select(DevProposal).where(
        DevProposal.id == proposal["id"]))).scalar_one()
    row.approved_patch_hash = "stale-" + (row.approved_patch_hash or "")
    await db_session.commit()

    resp = await client.post(f"/api/dev/proposals/{proposal['id']}/merge", headers=auth)
    assert resp.status_code == 409
    assert "approval" in resp.json()["detail"].lower() or "changed" in resp.json()["detail"].lower()
    detail = (await client.get(f"/api/dev/proposals/{proposal['id']}", headers=auth)).json()
    assert detail["status"] == "patch_ready"  # demoted; needs fresh approval
    assert detail["approved_patch_hash"] is None


async def test_dev_merge_requires_approval(client, auth):
    """Never merge without approval; silence is never consent (§26)."""
    await setup_mock_provider(client, auth, responses={"Standard weekly review": REVIEW_JSON})
    ins = (await client.post("/api/instructions", headers=auth, json=WEEKLY_REVIEW)).json()
    await client.post(f"/api/instructions/{ins['id']}/run", headers=auth)
    proposal = (await client.get("/api/dev/proposals", headers=auth)).json()[0]
    resp = await client.post(f"/api/dev/proposals/{proposal['id']}/merge", headers=auth)
    assert resp.status_code == 409


def test_secret_paths_never_reach_dev_context():
    assert dev_svc._is_secret_path(".env")
    assert dev_svc._is_secret_path(".env.production")
    assert dev_svc._is_secret_path("config/service.key")
    assert dev_svc._is_secret_path("backend/secrets.py")
    assert not dev_svc._is_secret_path("backend/core/models.py")
