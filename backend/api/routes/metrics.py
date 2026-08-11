"""Operational metrics endpoints (third pass §2, §27, §51-§52)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException

from backend.core.security import DB, CurrentUser
from backend.ops import metrics as metrics_svc

router = APIRouter(prefix="/metrics")

WINDOWS = {"today": 1, "week": 7, "month": 30}


@router.get("/overview")
async def overview(user: CurrentUser, db: DB):
    return await metrics_svc.overview(db, user.id)


@router.get("/usage")
async def usage(user: CurrentUser, db: DB, window: str = "week"):
    if window not in WINDOWS:
        raise HTTPException(400, f"window must be one of {list(WINDOWS)}")
    now = datetime.now(UTC)
    since = (now.replace(hour=0, minute=0, second=0, microsecond=0) if window == "today"
             else now - timedelta(days=WINDOWS[window]))
    totals = await metrics_svc.usage_totals(db, user.id, since=since)
    runtime = await metrics_svc.runtime_seconds(db, user.id, since=since)
    breakdowns = {}
    for dim in ("agent", "project", "provider", "model", "day"):
        breakdowns[dim] = await metrics_svc.usage_breakdown(db, user.id, since=since,
                                                            dimension=dim)
    return {"window": window, "since": since, "totals": totals, "runtime": runtime,
            "breakdowns": breakdowns,
            "byok_note": "AI provider usage is paid directly through your connected "
                         "provider accounts; it is not part of the Moseisley subscription."}
