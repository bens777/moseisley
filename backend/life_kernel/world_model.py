"""World Model foundations (§15): structured snapshot of current reality.

Deterministic assembly from canonical DB state. LLM interpretations elsewhere are
beliefs unless backed by evidence; this module only reports facts from the database.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import (
    ApprovalRequest,
    Experiment,
    Goal,
    Opportunity,
    Prediction,
    Project,
    SpendIntent,
    XRayFinding,
)


async def snapshot(db: AsyncSession, user_id: str) -> dict:
    now = datetime.now(UTC)
    goals = list((await db.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active")
    )).scalars())
    projects = list((await db.execute(
        select(Project).where(Project.user_id == user_id, Project.status.in_(["active", "experiment", "hold"]))
    )).scalars())
    experiments = list((await db.execute(
        select(Experiment).where(Experiment.user_id == user_id, Experiment.status == "running")
    )).scalars())
    opportunities = list((await db.execute(
        select(Opportunity).where(Opportunity.user_id == user_id,
                                  Opportunity.status.in_(["detected", "micro_test", "validated"]))
    )).scalars())
    pending_approvals = (await db.execute(
        select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending"
        )
    )).scalar_one()
    open_predictions = (await db.execute(
        select(func.count()).select_from(Prediction).where(
            Prediction.user_id == user_id, Prediction.status == "open"
        )
    )).scalar_one()
    month_ago = now - timedelta(days=30)
    findings = list((await db.execute(
        select(XRayFinding).where(XRayFinding.user_id == user_id, XRayFinding.created_at >= month_ago,
                                  XRayFinding.status == "open")
    )).scalars())
    spend_month = (await db.execute(
        select(func.coalesce(func.sum(SpendIntent.amount_cents), 0)).where(
            SpendIntent.user_id == user_id, SpendIntent.status == "executed",
            SpendIntent.created_at >= month_ago,
        )
    )).scalar_one()

    return {
        "as_of": now.isoformat(),
        "goals": [
            {"id": g.id, "title": g.title, "metric": g.metric, "target": g.target_value,
             "unit": g.unit, "deadline": g.deadline, "progress": g.progress,
             "confidence": g.confidence, "constraints": g.constraints_json}
            for g in goals
        ],
        "projects": [
            {"id": p.id, "name": p.name, "status": p.status, "strategy": p.strategy}
            for p in projects
        ],
        "running_experiments": [
            {"id": e.id, "hypothesis": e.hypothesis, "deadline": e.deadline,
             "success_criterion": e.success_criterion, "kill_criterion": e.kill_criterion}
            for e in experiments
        ],
        "open_opportunities": [
            {"id": o.id, "title": o.title, "status": o.status, "confidence": o.confidence}
            for o in opportunities
        ],
        "open_xray_findings": [
            {"id": f.id, "type": f.type, "title": f.title, "verified": f.verified,
             "estimated_value_cents": f.estimated_value_cents,
             "estimated_time_minutes": f.estimated_time_minutes}
            for f in findings
        ],
        "pending_approvals": int(pending_approvals),
        "open_predictions": int(open_predictions),
        "spend_executed_last_30d_cents": int(spend_month),
    }
