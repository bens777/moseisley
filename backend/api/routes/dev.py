"""Dev Agent API (third pass §20-§26)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from backend.agents import dev as dev_svc
from backend.core.models import DevProposal
from backend.core.security import DB, CurrentUser

router = APIRouter(prefix="/dev")


def _serialize(p: DevProposal) -> dict:
    return {
        "id": p.id, "title": p.title, "why": p.why,
        "expected_benefit": p.expected_benefit, "evidence": p.evidence_json,
        "plan_md": p.plan_md, "files_affected": p.files_affected_json,
        "schema_impact": p.schema_impact, "risk": p.risk, "test_plan": p.test_plan,
        "status": p.status, "branch_name": p.branch_name, "patch_hash": p.patch_hash,
        "patch_stats": p.patch_stats_json, "test_results": p.test_results_json,
        "approval_id": p.approval_id, "approved_patch_hash": p.approved_patch_hash,
        "approved_at": p.approved_at, "merged_commit": p.merged_commit,
        "created_at": p.created_at, "updated_at": p.updated_at,
    }


@router.get("/proposals")
async def list_proposals(user: CurrentUser, db: DB, status: str | None = None):
    q = (select(DevProposal).where(DevProposal.user_id == user.id)
         .order_by(DevProposal.created_at.desc()))
    if status:
        q = q.where(DevProposal.status == status)
    return [_serialize(p) for p in (await db.execute(q)).scalars()]


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, user: CurrentUser, db: DB):
    try:
        return _serialize(await dev_svc.get_proposal(db, user.id, proposal_id))
    except dev_svc.DevError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/proposals/{proposal_id}/prepare-patch")
async def prepare_patch(proposal_id: str, user: CurrentUser, db: DB):
    try:
        result = await dev_svc.prepare_patch(db, user, proposal_id)
    except dev_svc.DevError as e:
        await db.commit()  # persist error details recorded on the proposal
        raise HTTPException(409, str(e)) from e
    await db.commit()
    return result


@router.post("/proposals/{proposal_id}/resolve")
async def resolve(proposal_id: str, user: CurrentUser, db: DB, approve: bool = True):
    try:
        p = await dev_svc.get_proposal(db, user.id, proposal_id)
        if not p.approval_id:
            raise dev_svc.DevError("no approval pending for this proposal")
        result = await dev_svc.resolve_approval(db, user.id, p.approval_id,
                                                approve=approve, channel="dashboard")
    except dev_svc.DevError as e:
        raise HTTPException(409, str(e)) from e
    await db.commit()
    return result


@router.post("/proposals/{proposal_id}/merge")
async def merge(proposal_id: str, user: CurrentUser, db: DB):
    try:
        result = await dev_svc.merge(db, user, proposal_id)
    except dev_svc.DevError as e:
        await db.commit()  # persist invalidation state changes (§25)
        raise HTTPException(409, str(e)) from e
    await db.commit()
    return result
