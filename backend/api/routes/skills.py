"""Skills API: the catalog, per-user state, and the two lifecycle actions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.skills import service as skills_svc

router = APIRouter(prefix="/skills")


@router.get("")
async def list_skills(user: CurrentUser, db: DB):
    rows = await skills_svc.list_for_user(db, user)
    await db.commit()
    return {"skills": rows}


class EnableRequest(BaseModel):
    config: dict = {}


@router.post("/{skill_id}/enable")
async def enable(skill_id: str, user: CurrentUser, db: DB,
                 body: EnableRequest | None = None):
    try:
        await skills_svc.enable(db, user, skill_id, (body.config if body else None))
    except skills_svc.SkillError as e:
        raise HTTPException(404, str(e)) from e
    except skills_svc.SkillGated as e:
        # the same 402 + reason the rest of the platform raises
        raise HTTPException(402, e.detail) from e
    await db.commit()
    return await _one(db, user, skill_id)


@router.post("/{skill_id}/disable")
async def disable(skill_id: str, user: CurrentUser, db: DB):
    try:
        await skills_svc.disable(db, user, skill_id)
    except skills_svc.SkillError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return await _one(db, user, skill_id)


async def _one(db, user, skill_id: str) -> dict:
    rows = await skills_svc.list_for_user(db, user)
    return next(r for r in rows if r["id"] == skill_id)
