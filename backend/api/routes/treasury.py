from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.billing import entitlements
from backend.core.models import AgentConfig, AgentPaymentBinding, SpendIntent
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.treasury import approvals
from backend.treasury import notify as treasury_notify
from backend.treasury import service as treasury

router = APIRouter()


def _serialize_budget(b) -> dict:
    return {
        "currency": b.currency, "monthly_limit_cents": b.monthly_limit_cents,
        "daily_limit_cents": b.daily_limit_cents,
        "per_transaction_hard_limit_cents": b.per_transaction_hard_limit_cents,
        "autonomous_threshold_cents": b.autonomous_threshold_cents,
        "approval_threshold_cents": b.approval_threshold_cents,
        "allowed_categories": b.allowed_categories, "blocked_categories": b.blocked_categories,
        "allowed_vendors": b.allowed_vendors, "blocked_vendors": b.blocked_vendors,
        "spending_enabled": b.spending_enabled,
    }


def _serialize_intent(i: SpendIntent) -> dict:
    return {
        "id": i.id, "amount_cents": i.amount_cents, "currency": i.currency,
        "purpose": i.purpose, "vendor": i.vendor, "category": i.category,
        "agent_id": i.agent_id, "project_id": i.project_id, "experiment_id": i.experiment_id,
        "status": i.status, "decision_reason": i.decision_reason,
        "approval_request_id": i.approval_request_id, "transaction_id": i.transaction_id,
        "created_at": i.created_at,
    }


@router.get("/treasury")
async def get_treasury(user: CurrentUser, db: DB):
    budget = await treasury.get_or_create_budget(db, user.id)
    now = datetime.now(UTC)
    spent_day = await treasury._spent_since(db, user.id, now - timedelta(days=1))
    spent_month = await treasury._spent_since(db, user.id, now - timedelta(days=30))
    await db.commit()
    return {
        "budget": _serialize_budget(budget),
        "spent_today_cents": spent_day,
        "spent_this_month_cents": spent_month,
    }


class BudgetUpdate(BaseModel):
    monthly_limit_cents: int | None = None
    daily_limit_cents: int | None = None
    per_transaction_hard_limit_cents: int | None = None
    autonomous_threshold_cents: int | None = None
    approval_threshold_cents: int | None = None
    allowed_categories: list[str] | None = None
    blocked_categories: list[str] | None = None
    allowed_vendors: list[str] | None = None
    blocked_vendors: list[str] | None = None
    spending_enabled: bool | None = None


@router.patch("/treasury")
async def update_treasury(body: BudgetUpdate, user: CurrentUser, db: DB):
    budget = await treasury.get_or_create_budget(db, user.id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(budget, k, v)
    if changes:
        await ledger.record(db, user.id, "kill_switch_changed", actor_type="user",
                            payload={"treasury_update": changes})
    await db.commit()
    return _serialize_budget(budget)


class AgentSpendingUpdate(BaseModel):
    agent_id: str
    spending_enabled: bool
    max_autonomous_cents: int | None = None


@router.post("/treasury/agent-binding")
async def set_agent_binding(body: AgentSpendingUpdate, user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "treasury")
    agent = (await db.execute(select(AgentConfig).where(
        AgentConfig.id == body.agent_id, AgentConfig.user_id == user.id
    ))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "agent not found")
    binding = (await db.execute(select(AgentPaymentBinding).where(
        AgentPaymentBinding.user_id == user.id, AgentPaymentBinding.agent_id == body.agent_id
    ))).scalar_one_or_none()
    if binding is None:
        binding = AgentPaymentBinding(user_id=user.id, agent_id=body.agent_id)
        db.add(binding)
    binding.spending_enabled = body.spending_enabled
    binding.max_autonomous_cents = body.max_autonomous_cents
    await db.commit()
    return {"agent_id": body.agent_id, "spending_enabled": binding.spending_enabled,
            "max_autonomous_cents": binding.max_autonomous_cents}


class SpendIntentRequest(BaseModel):
    amount_cents: int
    currency: str = "EUR"
    purpose: str
    agent_id: str | None = None
    category: str | None = None
    vendor: str | None = None
    project_id: str | None = None
    experiment_id: str | None = None


@router.post("/spend-intents")
async def create_spend_intent(body: SpendIntentRequest, user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "treasury")
    intent = await treasury.create_spend_intent(db, user.id, actor_type="user",
                                                **body.model_dump())
    if intent.status == "awaiting_approval" and intent.approval_request_id:
        approval = await approvals.get(db, user.id, intent.approval_request_id)
        if approval is not None:
            await treasury_notify.notify_spend_approval(db, user.id, approval, intent)
    await db.commit()
    return _serialize_intent(intent)


@router.get("/spend-intents")
async def list_spend_intents(user: CurrentUser, db: DB, status: str | None = None):
    q = select(SpendIntent).where(SpendIntent.user_id == user.id)
    if status:
        q = q.where(SpendIntent.status == status)
    rows = (await db.execute(q.order_by(SpendIntent.created_at.desc()).limit(100))).scalars()
    return [_serialize_intent(i) for i in rows]


@router.get("/approvals")
async def list_approvals(user: CurrentUser, db: DB):
    rows = await approvals.list_pending(db, user.id)
    await db.commit()
    return [
        {"id": a.id, "action_type": a.action_type, "payload": a.action_payload_json,
         "risk_level": a.risk_level, "status": a.status, "expires_at": a.expires_at,
         "created_at": a.created_at}
        for a in rows
    ]


class ResolveRequest(BaseModel):
    approve: bool


@router.post("/approvals/{approval_id}/resolve")
async def resolve_approval(approval_id: str, body: ResolveRequest, user: CurrentUser, db: DB):
    try:
        result = await approvals.resolve(db, user.id, approval_id, approve=body.approve,
                                         channel="dashboard")
    except approvals.ApprovalError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return {"result": result}


class SimulateRequest(BaseModel):
    cases: list[dict]


@router.post("/treasury/simulate")
async def simulate(body: SimulateRequest, user: CurrentUser, db: DB):
    """Treasury simulator (§77): pure policy evaluation against current budget state.
    No intents are persisted and no money moves."""
    results = await treasury.simulate(db, user.id, body.cases[:50])
    await db.rollback()  # simulator never persists
    return {"results": results}
