from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import select

from backend.agents import crew
from backend.core.models import LlmUsage
from backend.core.security import DB, CurrentUser

router = APIRouter()


def _aggregate(rows: list[LlmUsage]) -> dict:
    agg = {"requests": 0, "total_tokens": 0, "reported_cost": 0.0, "estimated_cost": 0.0,
           "unknown_cost_requests": 0, "failed_requests": 0}
    for r in rows:
        agg["requests"] += 1
        if r.status == "failed":
            agg["failed_requests"] += 1
            continue
        agg["total_tokens"] += r.total_tokens or ((r.input_tokens or 0) + (r.output_tokens or 0))
        if r.cost_source == "PROVIDER_REPORTED" and r.provider_reported_cost is not None:
            agg["reported_cost"] += r.provider_reported_cost
        elif r.cost_source == "ESTIMATED" and r.estimated_cost is not None:
            agg["estimated_cost"] += r.estimated_cost
        else:
            agg["unknown_cost_requests"] += 1
    agg["reported_cost"] = round(agg["reported_cost"], 6)
    agg["estimated_cost"] = round(agg["estimated_cost"], 6)
    return agg


@router.get("/usage/summary")
async def usage_summary(user: CurrentUser, db: DB):
    """AI usage backed exclusively by persisted usage events (§35).
    Costs are labeled by source; nothing is invented."""
    now = datetime.now(UTC)
    month_rows = list((await db.execute(select(LlmUsage).where(
        LlmUsage.user_id == user.id, LlmUsage.created_at >= now - timedelta(days=30)
    ))).scalars())
    today_rows = [r for r in month_rows
                  if (r.created_at.replace(tzinfo=UTC) if r.created_at.tzinfo is None
                      else r.created_at) >= now - timedelta(days=1)]
    return {
        "today": _aggregate(today_rows),
        "month": _aggregate(month_rows),
        "by_role": await crew.role_usage_this_month(db, user.id),
        "currency": "USD",
        "note": "reported = provider-billed; estimated = from official pricing snapshots; "
                "unknown = no reliable pricing data",
    }


@router.get("/usage/events")
async def usage_events(user: CurrentUser, db: DB, limit: int = 50):
    rows = list((await db.execute(
        select(LlmUsage).where(LlmUsage.user_id == user.id)
        .order_by(LlmUsage.created_at.desc()).limit(min(limit, 200))
    )).scalars())
    return [
        {"id": r.id, "provider": r.provider, "requested_model": r.requested_model,
         "actual_model": r.model, "crew_role": r.crew_role, "purpose": r.purpose,
         "input_tokens": r.input_tokens, "cached_input_tokens": r.cached_input_tokens,
         "output_tokens": r.output_tokens, "reasoning_tokens": r.reasoning_tokens,
         "total_tokens": r.total_tokens,
         "provider_reported_cost": r.provider_reported_cost,
         "estimated_cost": r.estimated_cost, "cost_source": r.cost_source,
         "status": r.status, "created_at": r.created_at}
        for r in rows
    ]
