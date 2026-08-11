"""Projects / Portfolio (third pass §9-§10, §42).

Each project is a real AI-operated activity with real-world asset links.
Portfolio numbers are aggregated from canonical records (revenue events,
spend intents, crew runs, llm usage) — never stored as display strings.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from backend.core.models import ApprovalRequest, CrewRun, Experiment, Instruction, Project
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.ops import metrics as metrics_svc
from backend.ops import revenue as revenue_svc

router = APIRouter(prefix="/projects")

ALLOWED_URL_KEYS = ("website", "repository", "checkout", "analytics", "other")
STATUSES = ("active", "experiment", "hold", "killed", "completed")


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    status: str = "active"
    strategy: str | None = None
    linked_goal_ids: list[str] = []
    urls: dict[str, str] = {}
    capital_allocated_cents: int = 0
    currency: str = "EUR"


class RevenueEntry(BaseModel):
    amount_cents: int = Field(gt=0)
    currency: str = "EUR"
    description: str = ""
    source: str = "manual"
    source_ref: str | None = None
    recurring: bool = False
    recurrence_interval: str | None = None
    occurred_at: datetime | None = None
    evidence: dict = {}


def _clean_urls(urls: dict[str, str]) -> dict[str, str]:
    return {k: v.strip() for k, v in urls.items() if k in ALLOWED_URL_KEYS and v and v.strip()}


async def _get(db, user_id: str, project_id: str) -> Project:
    p = (await db.execute(select(Project).where(
        Project.id == project_id, Project.user_id == user_id))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "project not found")
    return p


async def project_metrics(db, user_id: str, project_id: str) -> dict:
    """Canonical per-project numbers — the same data the Orchestrator reads."""
    week_ago = datetime.now(UTC) - timedelta(days=7)
    runtime = await metrics_svc.runtime_seconds(db, user_id, project_id=project_id)
    runtime_week = await metrics_svc.runtime_seconds(db, user_id, since=week_ago,
                                                     project_id=project_id)
    usage = await metrics_svc.usage_totals(db, user_id, project_id=project_id)
    deployed = await metrics_svc.capital_deployed_cents(db, user_id, project_id=project_id)
    revenue = await revenue_svc.verified_revenue(db, user_id, days=30, project_id=project_id)
    mrr = await revenue_svc.verified_mrr(db, user_id, project_id=project_id)
    crew_roles = [r[0] for r in (await db.execute(
        select(CrewRun.crew_role).where(CrewRun.user_id == user_id,
                                        CrewRun.project_id == project_id).distinct())).all()]
    pending_rows = (await db.execute(select(ApprovalRequest).where(
        ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending"))).scalars()
    pending = sum(1 for a in pending_rows
                  if (a.action_payload_json or {}).get("project_id") == project_id)
    experiments = int((await db.execute(
        select(func.count(Experiment.id)).where(
            Experiment.user_id == user_id, Experiment.project_id == project_id)
    )).scalar_one())
    instructions = int((await db.execute(
        select(func.count(Instruction.id)).where(
            Instruction.user_id == user_id, Instruction.project_id == project_id,
            Instruction.enabled.is_(True))
    )).scalar_one())
    return {
        "runtime_total_seconds": runtime["total_seconds"],
        "runtime_week_seconds": runtime_week["total_seconds"],
        "operations": runtime["runs"],
        "ai_cost": {"reported": usage["reported_cost"], "estimated": usage["estimated_cost"],
                    "unknown_requests": usage["unknown_cost_requests"],
                    "currency": usage["cost_currency"]},
        "ai_tokens_total": usage["tokens"]["total"],
        "capital_deployed_cents": deployed,
        "verified_revenue_month": revenue,
        "verified_mrr": mrr,
        "crew_roles": crew_roles,
        "pending_approvals": pending,
        "experiments": experiments,
        "active_instructions": instructions,
    }


def _serialize(p: Project) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description, "status": p.status,
        "strategy": p.strategy, "linked_goal_ids": p.linked_goal_ids or [],
        "urls": p.urls_json or {}, "currency": p.currency,
        "capital_allocated_cents": p.capital_allocated_cents,
        "created_at": p.created_at, "updated_at": p.updated_at,
    }


@router.get("")
async def portfolio(user: CurrentUser, db: DB):
    rows = list((await db.execute(select(Project).where(Project.user_id == user.id)
                                  .order_by(Project.created_at))).scalars())
    out = []
    for p in rows:
        out.append({**_serialize(p), "metrics": await project_metrics(db, user.id, p.id)})
    return out


@router.post("")
async def create_project(body: ProjectRequest, user: CurrentUser, db: DB):
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    p = Project(user_id=user.id, name=body.name, description=body.description,
                status=body.status, strategy=body.strategy,
                linked_goal_ids=body.linked_goal_ids, urls_json=_clean_urls(body.urls),
                capital_allocated_cents=body.capital_allocated_cents,
                currency=body.currency.upper()[:3])
    db.add(p)
    await db.flush()
    await ledger.record(db, user.id, "project_created", actor_type="user",
                        entity_type="project", entity_id=p.id, payload={"name": p.name})
    await db.commit()
    return _serialize(p)


@router.get("/{project_id}")
async def get_project(project_id: str, user: CurrentUser, db: DB):
    p = await _get(db, user.id, project_id)
    return {**_serialize(p), "metrics": await project_metrics(db, user.id, p.id),
            "revenue_events": [
                {"id": e.id, "source": e.source, "source_ref": e.source_ref,
                 "description": e.description, "amount_cents": e.amount_cents,
                 "currency": e.currency, "occurred_at": e.occurred_at,
                 "recurring": e.recurring, "recurrence_interval": e.recurrence_interval,
                 "verification_status": e.verification_status,
                 "manual": e.source == "manual", "last_synced_at": e.last_synced_at}
                for e in await revenue_svc.list_events(db, user.id, project_id=p.id, limit=50)
            ]}


@router.patch("/{project_id}")
async def update_project(project_id: str, body: ProjectRequest, user: CurrentUser, db: DB):
    p = await _get(db, user.id, project_id)
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    p.name, p.description, p.status = body.name, body.description, body.status
    p.strategy, p.linked_goal_ids = body.strategy, body.linked_goal_ids
    p.urls_json = _clean_urls(body.urls)
    p.capital_allocated_cents = body.capital_allocated_cents
    p.currency = body.currency.upper()[:3]
    await ledger.record(db, user.id, "project_updated", actor_type="user",
                        entity_type="project", entity_id=p.id, payload={"name": p.name})
    await db.commit()
    return _serialize(p)


@router.post("/{project_id}/revenue")
async def record_revenue(project_id: str, body: RevenueEntry, user: CurrentUser, db: DB):
    """Record verified revenue. Manual entries are labeled MANUAL everywhere —
    only connected-source syncs may claim an integration source."""
    p = await _get(db, user.id, project_id)
    if body.source != "manual":
        raise HTTPException(400, "only source='manual' may be recorded by hand; "
                                 "connected sources sync automatically")
    try:
        event = await revenue_svc.record_event(
            db, user.id, source="manual", amount_cents=body.amount_cents,
            currency=body.currency, occurred_at=body.occurred_at, project_id=p.id,
            source_ref=body.source_ref, description=body.description,
            recurring=body.recurring, recurrence_interval=body.recurrence_interval,
            evidence=body.evidence)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return {"id": event.id, "recorded": True, "source": "manual"}


@router.post("/{project_id}/revenue/{event_id}/reverse")
async def reverse_revenue(project_id: str, event_id: str, user: CurrentUser, db: DB):
    await _get(db, user.id, project_id)
    try:
        await revenue_svc.reverse_event(db, user.id, event_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return {"reversed": True}
