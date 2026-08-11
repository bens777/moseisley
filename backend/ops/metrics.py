"""Deterministic operational metrics (third pass §2, §51, §54-§55).

Everything here is aggregated from canonical records:

- AGENT RUNTIME   = sum(finished_at - started_at) over crew_runs. Real
  execution time; concurrent runs may sum past wall-clock (that is correct
  and documented). Never presented as "time saved".
- AI TOKENS/COST  = llm_usage rows (tokens exactly as providers reported;
  cost split into PROVIDER_REPORTED / ESTIMATED, with UNKNOWN counted).
- CAPITAL DEPLOYED = executed spend intents (Treasury transactions).
- TREASURY AVAILABLE = deterministic budget limits minus counted spend.
- VERIFIED REVENUE / MRR = backend/ops/revenue.py methodology.
- OPERATIONS COMPLETED = completed crew_runs.
- PENDING APPROVALS / ACTIVE PROJECTS = live counts.

No LLM is involved anywhere in this module.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import (
    ApprovalRequest,
    Budget,
    CrewRun,
    LlmUsage,
    Project,
    SpendIntent,
)
from backend.ops import revenue as revenue_svc
from backend.treasury.service import COUNTED_STATUSES, get_or_create_budget

DEPLOYED_STATUSES = ("executed",)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _effective_total(u: LlmUsage) -> int:
    """Provider-reported total when present, else the sum of reported parts.
    Derived only from recorded provider data — never guessed."""
    if u.total_tokens:
        return u.total_tokens
    return (u.input_tokens or 0) + (u.output_tokens or 0) + (u.reasoning_tokens or 0)


async def runtime_seconds(db: AsyncSession, user_id: str, *, since: datetime | None = None,
                          project_id: str | None = None) -> dict:
    """Actual crew execution seconds, total and per role."""
    q = select(CrewRun).where(CrewRun.user_id == user_id, CrewRun.finished_at.isnot(None))
    if since is not None:
        q = q.where(CrewRun.started_at >= since)
    if project_id is not None:
        q = q.where(CrewRun.project_id == project_id)
    total = 0.0
    by_role: dict[str, float] = {}
    runs = 0
    for run in (await db.execute(q)).scalars():
        started, finished = _aware(run.started_at), _aware(run.finished_at)
        if started is None or finished is None or finished < started:
            continue
        seconds = (finished - started).total_seconds()
        total += seconds
        by_role[run.crew_role] = by_role.get(run.crew_role, 0.0) + seconds
        runs += 1
    return {"total_seconds": round(total, 1), "runs": runs,
            "by_role": {k: round(v, 1) for k, v in sorted(by_role.items(), key=lambda x: -x[1])}}


async def usage_totals(db: AsyncSession, user_id: str, *, since: datetime | None = None,
                       project_id: str | None = None) -> dict:
    """Token and cost totals from recorded llm_usage. NULL-safe, never invented."""
    q = select(LlmUsage).where(LlmUsage.user_id == user_id)
    if since is not None:
        q = q.where(LlmUsage.created_at >= since)
    if project_id is not None:
        q = q.where(LlmUsage.project_id == project_id)
    tokens = {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0, "total": 0}
    reported = 0.0
    estimated = 0.0
    unknown_requests = 0
    requests = 0
    for u in (await db.execute(q)).scalars():
        requests += 1
        for field, key in (("input_tokens", "input"), ("cached_input_tokens", "cached_input"),
                           ("output_tokens", "output"), ("reasoning_tokens", "reasoning")):
            v = getattr(u, field)
            if v:
                tokens[key] += v
        # effective total: provider total when reported, else sum of reported parts
        tokens["total"] += _effective_total(u)
        if u.cost_source == "PROVIDER_REPORTED" and u.provider_reported_cost is not None:
            reported += u.provider_reported_cost
        elif u.cost_source == "ESTIMATED" and u.estimated_cost is not None:
            estimated += u.estimated_cost
        else:
            unknown_requests += 1
    return {"requests": requests, "tokens": tokens,
            "reported_cost": round(reported, 4), "estimated_cost": round(estimated, 4),
            "unknown_cost_requests": unknown_requests, "cost_currency": "USD"}


async def usage_breakdown(db: AsyncSession, user_id: str, *, since: datetime,
                          dimension: str) -> list[dict]:
    """Grouped usage: dimension in agent|project|provider|model|day."""
    col = {"agent": LlmUsage.crew_role, "project": LlmUsage.project_id,
           "provider": LlmUsage.provider, "model": LlmUsage.model,
           "day": func.date(LlmUsage.created_at)}.get(dimension)
    if col is None:
        raise ValueError(f"unknown dimension: {dimension}")
    effective_total = func.coalesce(
        LlmUsage.total_tokens,
        func.coalesce(LlmUsage.input_tokens, 0)
        + func.coalesce(LlmUsage.output_tokens, 0)
        + func.coalesce(LlmUsage.reasoning_tokens, 0))
    rows = (await db.execute(
        select(col.label("key"),
               func.count(LlmUsage.id),
               func.coalesce(func.sum(effective_total), 0),
               func.coalesce(func.sum(LlmUsage.provider_reported_cost), 0.0),
               func.coalesce(func.sum(LlmUsage.estimated_cost), 0.0))
        .where(LlmUsage.user_id == user_id, LlmUsage.created_at >= since)
        .group_by(col)
    )).all()
    out = [{"key": str(r[0]) if r[0] is not None else None, "requests": r[1],
            "total_tokens": int(r[2]), "reported_cost": round(float(r[3]), 4),
            "estimated_cost": round(float(r[4]), 4)} for r in rows]
    return sorted(out, key=lambda r: -(r["reported_cost"] + r["estimated_cost"]))


async def capital_deployed_cents(db: AsyncSession, user_id: str,
                                 project_id: str | None = None) -> int:
    """Money actually spent by the crew: executed spend intents only."""
    q = select(func.coalesce(func.sum(SpendIntent.amount_cents), 0)).where(
        SpendIntent.user_id == user_id, SpendIntent.status.in_(DEPLOYED_STATUSES))
    if project_id is not None:
        q = q.where(SpendIntent.project_id == project_id)
    return int((await db.execute(q)).scalar_one())


async def treasury_available_cents(db: AsyncSession, user_id: str) -> dict:
    """What the crew is still authorized to spend under deterministic budgets."""
    budget: Budget = await get_or_create_budget(db, user_id)
    now = datetime.now(UTC)

    async def spent_since(since: datetime) -> int:
        return int((await db.execute(
            select(func.coalesce(func.sum(SpendIntent.amount_cents), 0)).where(
                SpendIntent.user_id == user_id,
                SpendIntent.status.in_(COUNTED_STATUSES),
                SpendIntent.created_at >= since)
        )).scalar_one())

    month_spent = await spent_since(now - timedelta(days=30))
    day_spent = await spent_since(now - timedelta(days=1))
    month_left = max((budget.monthly_limit_cents or 0) - month_spent, 0)
    day_left = max((budget.daily_limit_cents or 0) - day_spent, 0)
    return {
        "spending_enabled": budget.spending_enabled,
        "currency": budget.currency,
        "monthly_limit_cents": budget.monthly_limit_cents,
        "daily_limit_cents": budget.daily_limit_cents,
        "month_spent_cents": month_spent,
        "available_cents": min(month_left, day_left) if budget.spending_enabled else 0,
        "monthly_available_cents": month_left,
    }


async def overview(db: AsyncSession, user_id: str) -> dict:
    """The Command Center KPI payload — real operational data only (§51)."""
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    runtime_week = await runtime_seconds(db, user_id, since=week_ago)
    usage_week = await usage_totals(db, user_id, since=week_ago)
    usage_today = await usage_totals(db, user_id, since=today_start)
    treasury = await treasury_available_cents(db, user_id)
    deployed = await capital_deployed_cents(db, user_id)
    rev_month = await revenue_svc.verified_revenue(db, user_id, days=30)
    mrr = await revenue_svc.verified_mrr(db, user_id)

    ops_completed = int((await db.execute(
        select(func.count(CrewRun.id)).where(
            CrewRun.user_id == user_id, CrewRun.status == "completed")
    )).scalar_one())
    pending_approvals = int((await db.execute(
        select(func.count(ApprovalRequest.id)).where(
            ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending")
    )).scalar_one())
    active_projects = int((await db.execute(
        select(func.count(Project.id)).where(
            Project.user_id == user_id, Project.status.in_(("active", "experiment")))
    )).scalar_one())

    return {
        "runtime_week": runtime_week,
        "usage_week": usage_week,
        "usage_today": usage_today,
        "treasury": treasury,
        "capital_deployed_cents": deployed,
        "verified_revenue_month": rev_month,   # per-currency cents
        "verified_mrr": mrr,                   # per-currency cents
        "operations_completed": ops_completed,
        "pending_approvals": pending_approvals,
        "active_projects": active_projects,
        "methodology": {
            "runtime": "sum of actual crew run start→finish durations; concurrent runs may exceed wall-clock",
            "tokens": "provider-reported usage records only",
            "cost": "PROVIDER_REPORTED preserved; ESTIMATED from pricing snapshots; otherwise UNKNOWN",
            "revenue": "verified events only — see backend/ops/revenue.py",
            "mrr": f"latest monthly recurring charge per source within {revenue_svc.MRR_LOOKBACK_DAYS} days",
        },
    }
