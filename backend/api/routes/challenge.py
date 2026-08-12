"""The Darvas Challenge — PUBLIC, no authentication.

Read-only. Exposes a simulated portfolio trading FICTIONAL money; there is no
broker connection and no real funds anywhere in this feature.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.challenge import service as challenge_svc
from backend.core.security import DB

public_router = APIRouter(prefix="/public/challenge")


@public_router.get("")
async def challenge_state(db: DB):
    return await challenge_svc.public_state(db)
