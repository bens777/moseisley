from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.models import Goal
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.life_kernel import goal_compiler
from backend.life_kernel.focus import rebuild_focus

router = APIRouter(prefix="/goals")


def _serialize(g: Goal) -> dict:
    return {
        "id": g.id, "title": g.title, "metric": g.metric, "target_value": g.target_value,
        "unit": g.unit, "currency": g.currency, "deadline": g.deadline,
        "constraints": g.constraints_json, "status": g.status, "progress": g.progress,
        "confidence": g.confidence, "created_at": g.created_at, "updated_at": g.updated_at,
    }


@router.get("")
async def list_goals(user: CurrentUser, db: DB, status: str | None = None):
    q = select(Goal).where(Goal.user_id == user.id)
    if status:
        q = q.where(Goal.status == status)
    return [_serialize(g) for g in (await db.execute(q.order_by(Goal.created_at))).scalars()]


class CompileRequest(BaseModel):
    text: str
    prior_extracted: dict | None = None


@router.post("/compile")
async def compile_goal(body: CompileRequest, user: CurrentUser, db: DB):
    result = await goal_compiler.compile_goal(db, user.id, body.text, prior_extracted=body.prior_extracted)
    await db.commit()
    return {
        "status": result.status,
        "question": result.question,
        "extracted": result.extracted,
        "goal": _serialize(result.goal) if result.goal else None,
    }


class UpdateGoalRequest(BaseModel):
    title: str | None = None
    target_value: float | None = None
    unit: str | None = None
    deadline: str | None = None
    constraints: dict | None = None
    status: str | None = None
    progress: float | None = None


@router.patch("/{goal_id}")
async def update_goal(goal_id: str, body: UpdateGoalRequest, user: CurrentUser, db: DB):
    goal = (
        await db.execute(select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id))
    ).scalar_one_or_none()
    if goal is None:
        raise HTTPException(404, "goal not found")
    changes = {}
    for field in ("title", "target_value", "unit", "deadline", "status", "progress"):
        value = getattr(body, field)
        if value is not None:
            changes[field] = value
            setattr(goal, field, value)
    if body.constraints is not None:
        goal.constraints_json = body.constraints
        changes["constraints"] = body.constraints
    if body.status is not None and body.status not in ("active", "paused", "achieved", "abandoned"):
        raise HTTPException(400, "invalid status")
    if changes:
        await ledger.record(db, user.id, "goal_updated", actor_type="user",
                            entity_type="goal", entity_id=goal.id, payload=changes)
        await rebuild_focus(db, user.id)
    await db.commit()
    return _serialize(goal)
