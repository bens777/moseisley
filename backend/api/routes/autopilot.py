from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from backend.billing import entitlements
from backend.core.security import DB, CurrentUser
from backend.strategy import autopilot

router = APIRouter(prefix="/autopilot")


@router.get("/loops")
async def list_loops():
    return {"loops": autopilot.LOOPS}


@router.post("/{loop}/run")
async def run_loop(loop: str, user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "autopilot")
    if loop not in autopilot.LOOPS:
        raise HTTPException(404, f"unknown loop: {loop}")
    result = await autopilot.run_loop(db, user, loop)
    await db.commit()
    return asdict(result)
