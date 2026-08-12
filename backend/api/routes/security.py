"""Security API: what screening did to external agent replies, and the queue of
things it held back.

Everything here is a view over agent_inspections plus one per-agent flag. The
screening itself lives in backend.agents.inspection.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.agents import inspection, runtimes
from backend.core.models import AgentConfig, AgentInspection
from backend.core.security import DB, CurrentUser

router = APIRouter(prefix="/security")

HEADER_NOTE = ("Screening reduces risk; no filter catches everything. External "
               "runtimes are always treated as untrusted.")


@router.get("")
async def overview(user: CurrentUser, db: DB, limit: int = 50):
    rows = list((await db.execute(
        select(AgentInspection).where(AgentInspection.user_id == user.id)
        .order_by(AgentInspection.created_at.desc()).limit(min(limit, 200))
    )).scalars())
    agents = list((await db.execute(
        select(AgentConfig).where(AgentConfig.user_id == user.id)
        .order_by(AgentConfig.created_at))).scalars())
    external = [a for a in agents if a.adapter_type != "native"]
    return {
        "note": HEADER_NOTE,
        "quarantined_count": await inspection.quarantined_count(db, user.id),
        "log": [inspection.serialize(r) for r in rows],
        "quarantine": [inspection.serialize(r, include_content=True)
                       for r in rows if r.status in ("quarantined", "blocked")],
        "agents": [{"id": a.id, "display_name": a.display_name,
                    "adapter_type": a.adapter_type,
                    "runtime_name": (runtimes.BY_ID.get(a.adapter_type) or {})
                        .get("name", a.adapter_type),
                    "strict": inspection.is_strict(a)} for a in external],
    }


@router.post("/inspections/{inspection_id}/approve")
async def approve(inspection_id: str, user: CurrentUser, db: DB):
    return await _resolve(db, user, inspection_id, approve=True)


@router.post("/inspections/{inspection_id}/discard")
async def discard(inspection_id: str, user: CurrentUser, db: DB):
    return await _resolve(db, user, inspection_id, approve=False)


async def _resolve(db, user, inspection_id: str, *, approve: bool):
    try:
        row = await inspection.resolve(db, user, inspection_id, approve=approve)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return inspection.serialize(row)


class StrictRequest(BaseModel):
    enabled: bool


@router.post("/agents/{agent_id}/strict")
async def set_strict(agent_id: str, body: StrictRequest, user: CurrentUser, db: DB):
    """Strict mode: hold every reply from this agent for manual review."""
    agent = (await db.execute(select(AgentConfig).where(
        AgentConfig.id == agent_id, AgentConfig.user_id == user.id))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "agent not found")
    if agent.adapter_type == "native":
        raise HTTPException(400, "the native agent runs in-platform and is not screened")
    agent.configuration_json = {**(agent.configuration_json or {}),
                                inspection.STRICT_KEY: body.enabled}
    await db.commit()
    return {"id": agent.id, "strict": body.enabled}
