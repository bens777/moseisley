from __future__ import annotations

from fastapi import APIRouter

from backend.billing import entitlements
from backend.core.security import DB, CurrentUser
from backend.strategy.auditor import run_weekly_review

router = APIRouter()


@router.post("/auditor/weekly-review")
async def weekly_review(user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "auditor")
    report = await run_weekly_review(db, user)
    await db.commit()
    return report
