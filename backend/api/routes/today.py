from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.billing import entitlements
from backend.core import killswitch
from backend.core.models import (
    ApprovalRequest,
    Budget,
    Event,
    Goal,
    SpendIntent,
    XRayFinding,
)
from backend.core.security import DB, CurrentUser
from backend.jobs import user_schedule
from backend.jobs.scheduler import ensure_default_schedules
from backend.strategy.strategist import run_daily_strategist
from backend.xray.provenance import synthetic_run_ids

router = APIRouter()


def _goal_trajectory(goals: list[Goal]) -> str:
    if not goals:
        return "NO GOALS"
    at_risk = False
    for g in goals:
        if g.confidence is not None and g.confidence < 0.5:
            at_risk = True
        elif g.deadline:
            try:
                deadline = datetime.fromisoformat(g.deadline).replace(tzinfo=UTC)
                created = g.created_at if g.created_at.tzinfo else g.created_at.replace(tzinfo=UTC)
                total = (deadline - created).total_seconds()
                elapsed = (datetime.now(UTC) - created).total_seconds()
                if total > 0 and elapsed / total > g.progress + 0.25:
                    at_risk = True
            except ValueError:
                pass
    return "AT RISK" if at_risk else "ON TRACK"


@router.get("/today")
async def today(user: CurrentUser, db: DB):
    await ensure_default_schedules(db, user.id, user.timezone,
                                   disabled=set(user_schedule.disabled_types(user)))
    month_ago = datetime.now(UTC) - timedelta(days=30)

    goals = list((await db.execute(
        select(Goal).where(Goal.user_id == user.id, Goal.status == "active")
    )).scalars())

    plan = await killswitch.get_setting(db, user.id, "latest_strategist_plan")

    # findings derived from the retired demo dataset stop counting immediately,
    # whether or not the user has cleared the connection yet
    synthetic_runs = await synthetic_run_ids(db, user.id)
    findings = [f for f in (await db.execute(
        select(XRayFinding).where(XRayFinding.user_id == user.id,
                                  XRayFinding.created_at >= month_ago)
    )).scalars() if f.run_id not in synthetic_runs]
    verified_money = sum(f.estimated_value_cents or 0 for f in findings
                         if f.type == "found_money" and f.verified)
    estimated_opportunity = sum(f.estimated_value_cents or 0 for f in findings
                                if f.type == "estimated_opportunity")
    time_minutes = sum(f.estimated_time_minutes or 0 for f in findings)

    market_event = (await db.execute(
        select(Event).where(Event.user_id == user.id, Event.event_type == "market_scan_completed")
        .order_by(Event.created_at.desc()).limit(1)
    )).scalars().first()
    market_status = "NOT YET SCANNED"
    if market_event is not None:
        market_status = market_event.payload_json.get("outcome", "NO MATERIAL CHANGE")

    budget = (await db.execute(
        select(Budget).where(Budget.user_id == user.id, Budget.scope == "treasury")
    )).scalar_one_or_none()
    spent_month = (await db.execute(
        select(func.coalesce(func.sum(SpendIntent.amount_cents), 0)).where(
            SpendIntent.user_id == user.id, SpendIntent.status == "executed",
            SpendIntent.created_at >= month_ago)
    )).scalar_one()

    pending_approvals = (await db.execute(
        select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.user_id == user.id, ApprovalRequest.status == "pending")
    )).scalar_one()

    handled = (await db.execute(
        select(func.count()).select_from(Event).where(
            Event.user_id == user.id, Event.created_at >= month_ago,
            Event.actor_type.in_(["agent", "system"]),
            Event.event_type.in_(["tool_executed", "autopilot_draft_created", "xray_completed",
                                  "strategy_proposed", "market_scan_completed"]),
        )
    )).scalar_one()

    switches = {s: await killswitch.is_on(db, user.id, s) for s in killswitch.ALL_SWITCHES}
    await db.commit()

    return {
        "goal_trajectory": _goal_trajectory(goals),
        "goals": [{"id": g.id, "title": g.title, "progress": g.progress,
                   "deadline": g.deadline, "confidence": g.confidence} for g in goals],
        "top_actions": (plan.get("top_priorities") or [])[:3] if plan else [],
        "no_action": bool(plan.get("no_action")) if plan else None,
        "strategist_summary": plan.get("summary") if plan else None,
        "value_found_this_month": {
            "verified_money_cents": verified_money,
            "estimated_opportunity_cents": estimated_opportunity,
            "estimated_time_recoverable_minutes": time_minutes,
        },
        "market_status": market_status,
        "treasury": {
            "monthly_limit_cents": budget.monthly_limit_cents if budget else None,
            "spent_this_month_cents": int(spent_month),
            "spending_enabled": bool(budget.spending_enabled) if budget else False,
        },
        "needs_you": int(pending_approvals),
        "handled_automatically": int(handled),
        "kill_switches": switches,
    }


@router.post("/strategist/run")
async def run_strategist_now(user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "strategist")
    plan = await run_daily_strategist(db, user)
    await db.commit()
    return plan
