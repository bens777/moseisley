"""Schedule API: the user's single source of truth for everything that recurs.

Read-and-two-edits over scheduled_jobs. All writes go through
backend.jobs.user_schedule, which delegates to the owning service (Instructions)
or to the scheduler's own helpers — no scheduling logic lives here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.jobs import user_schedule

router = APIRouter(prefix="/schedule")


@router.get("")
async def list_schedule(user: CurrentUser, db: DB):
    rows = await user_schedule.list_for_user(db, user)
    await db.commit()
    return {"timezone": user.timezone, "jobs": rows}


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/{job_id}/toggle")
async def toggle(job_id: str, body: ToggleRequest, user: CurrentUser, db: DB):
    try:
        job = await user_schedule.toggle(db, user, job_id, body.enabled)
    except user_schedule.ScheduleError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return job


class CadenceRequest(BaseModel):
    frequency: str          # hourly | daily | weekly
    time: str = "08:00"     # local HH:MM (ignored for hourly)
    weekday: int = 0        # 0 = Monday (weekly only)


@router.put("/{job_id}/cadence")
async def set_cadence(job_id: str, body: CadenceRequest, user: CurrentUser, db: DB):
    try:
        job = await user_schedule.set_cadence(db, user, job_id, frequency=body.frequency,
                                              time=body.time, weekday=body.weekday)
    except user_schedule.ScheduleError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return job
