"""Treasury (§72-74): deterministic policy code. No LLM can override any decision here.

Decision order for a SpendIntent:
  spending switches → amount sanity → hard limit → vendor/category rules →
  daily/monthly windows → agent-specific limits → autonomous vs approval threshold.
Every step is integer/boolean arithmetic on database state.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch
from backend.core.models import (
    AgentPaymentBinding,
    ApprovalRequest,
    Budget,
    SpendIntent,
    Transaction,
)
from backend.ledger import service as ledger
from backend.treasury import approvals
from backend.treasury.payments import get_payment_provider

COUNTED_STATUSES = ("auto_approved", "approved", "executed", "awaiting_approval")


async def get_or_create_budget(db: AsyncSession, user_id: str) -> Budget:
    budget = (await db.execute(select(Budget).where(
        Budget.user_id == user_id, Budget.scope == "treasury"
    ))).scalar_one_or_none()
    if budget is None:
        budget = Budget(user_id=user_id, scope="treasury", spending_enabled=False)
        db.add(budget)
        await db.flush()
    return budget


async def _spent_since(db: AsyncSession, user_id: str, since: datetime) -> int:
    total = (await db.execute(
        select(func.coalesce(func.sum(SpendIntent.amount_cents), 0)).where(
            SpendIntent.user_id == user_id,
            SpendIntent.status.in_(COUNTED_STATUSES),
            SpendIntent.created_at >= since,
        )
    )).scalar_one()
    return int(total)


async def evaluate(
    db: AsyncSession, user_id: str, *, amount_cents: int, agent_id: str | None = None,
    category: str | None = None, vendor: str | None = None,
) -> tuple[str, str]:
    """Pure deterministic decision: returns (decision, reason).
    decision: auto_approve | require_approval | deny
    """
    budget = await get_or_create_budget(db, user_id)

    if await killswitch.is_on(db, user_id, killswitch.EMERGENCY_STOP):
        return "deny", "EMERGENCY STOP is engaged"
    if await killswitch.is_on(db, user_id, killswitch.DISABLE_SPENDING):
        return "deny", "global spending kill switch is engaged"
    if not budget.spending_enabled:
        return "deny", "spending is disabled"
    if amount_cents <= 0:
        return "deny", "amount must be positive"
    if budget.per_transaction_hard_limit_cents is not None and \
            amount_cents > budget.per_transaction_hard_limit_cents:
        return "deny", (f"amount exceeds hard per-transaction limit "
                        f"({budget.per_transaction_hard_limit_cents} cents)")
    if vendor and vendor in (budget.blocked_vendors or []):
        return "deny", f"vendor '{vendor}' is blocked"
    if category and category in (budget.blocked_categories or []):
        return "deny", f"category '{category}' is blocked"
    if budget.allowed_vendors and vendor not in budget.allowed_vendors:
        return "deny", f"vendor '{vendor}' is not on the allowed list"
    if budget.allowed_categories and category not in budget.allowed_categories:
        return "deny", f"category '{category}' is not on the allowed list"

    now = datetime.now(UTC)
    if budget.daily_limit_cents is not None:
        spent_day = await _spent_since(db, user_id, now - timedelta(days=1))
        if spent_day + amount_cents > budget.daily_limit_cents:
            return "deny", "daily budget exceeded"
    if budget.monthly_limit_cents is not None:
        spent_month = await _spent_since(db, user_id, now - timedelta(days=30))
        if spent_month + amount_cents > budget.monthly_limit_cents:
            return "deny", "monthly budget exceeded"

    agent_max_autonomous: int | None = None
    if agent_id is not None:
        binding = (await db.execute(select(AgentPaymentBinding).where(
            AgentPaymentBinding.user_id == user_id, AgentPaymentBinding.agent_id == agent_id
        ))).scalar_one_or_none()
        if binding is None or not binding.spending_enabled:
            return "deny", "spending is disabled for this agent"
        agent_max_autonomous = binding.max_autonomous_cents

    autonomous_limit = budget.autonomous_threshold_cents
    if agent_max_autonomous is not None:
        autonomous_limit = min(autonomous_limit or agent_max_autonomous, agent_max_autonomous)

    if autonomous_limit is not None and amount_cents <= autonomous_limit:
        return "auto_approve", f"within autonomous threshold ({autonomous_limit} cents)"

    approval_limit = budget.approval_threshold_cents
    if approval_limit is None or amount_cents <= approval_limit:
        return "require_approval", "amount requires human approval"
    return "deny", f"amount exceeds approval threshold ({approval_limit} cents)"


async def create_spend_intent(
    db: AsyncSession, user_id: str, *, amount_cents: int, currency: str = "EUR",
    purpose: str, agent_id: str | None = None, category: str | None = None,
    vendor: str | None = None, project_id: str | None = None, experiment_id: str | None = None,
    actor_type: str = "agent",
) -> SpendIntent:
    intent = SpendIntent(
        user_id=user_id, amount_cents=amount_cents, currency=currency, purpose=purpose,
        agent_id=agent_id, category=category, vendor=vendor,
        project_id=project_id, experiment_id=experiment_id,
    )
    db.add(intent)
    await db.flush()
    await ledger.record(db, user_id, "spend_requested", actor_type=actor_type, actor_id=agent_id,
                        entity_type="spend_intent", entity_id=intent.id,
                        payload={"amount_cents": amount_cents, "currency": currency,
                                 "purpose": purpose, "vendor": vendor})

    decision, reason = await evaluate(db, user_id, amount_cents=amount_cents, agent_id=agent_id,
                                      category=category, vendor=vendor)
    intent.decision_reason = reason
    if decision == "deny":
        intent.status = "denied"
        await ledger.record(db, user_id, "spend_denied", entity_type="spend_intent",
                            entity_id=intent.id, payload={"reason": reason})
    elif decision == "auto_approve":
        intent.status = "auto_approved"
        await ledger.record(db, user_id, "spend_approved", entity_type="spend_intent",
                            entity_id=intent.id, payload={"mode": "autonomous", "reason": reason})
        await execute_spend(db, intent)
    else:
        approval = await approvals.create(
            db, user_id, "spend",
            {"spend_intent_id": intent.id, "amount_cents": amount_cents,
             "currency": currency, "purpose": purpose, "vendor": vendor},
            risk_level=5,
        )
        intent.status = "awaiting_approval"
        intent.approval_request_id = approval.id
    await db.flush()
    return intent


async def execute_spend(db: AsyncSession, intent: SpendIntent) -> Transaction:
    """Executes an approved intent through the PaymentProvider. Status is reported
    truthfully (§112): SUCCESS / FAILED / UNKNOWN as returned by the provider."""
    if intent.status not in ("auto_approved", "approved"):
        raise ValueError(f"cannot execute spend intent in status {intent.status}")
    provider = get_payment_provider()
    status, ref = await provider.execute(intent)
    tx = Transaction(
        user_id=intent.user_id, spend_intent_id=intent.id, provider=provider.provider_name,
        provider_transaction_ref=ref or None, amount_cents=intent.amount_cents,
        currency=intent.currency,
        status={"SUCCESS": "success", "FAILED": "failed"}.get(status, "unknown"),
    )
    db.add(tx)
    await db.flush()
    intent.transaction_id = tx.id
    if status == "SUCCESS":
        intent.status = "executed"
    elif status == "FAILED":
        intent.status = "failed"
    # UNKNOWN keeps the approved status; §112: never claim success without confirmation
    await ledger.record(db, intent.user_id, "spend_executed", entity_type="spend_intent",
                        entity_id=intent.id,
                        payload={"status": status, "provider": provider.provider_name,
                                 "amount_cents": intent.amount_cents})
    return tx


async def _spend_approval_executor(db: AsyncSession, user_id: str,
                                   approval: ApprovalRequest, approved: bool) -> str:
    intent_id = (approval.action_payload_json or {}).get("spend_intent_id")
    intent = (await db.execute(select(SpendIntent).where(
        SpendIntent.id == intent_id, SpendIntent.user_id == user_id
    ))).scalar_one_or_none()
    if intent is None:
        return "Spend intent not found."
    if intent.status != "awaiting_approval":
        return f"Spend intent already {intent.status}."
    amount = f"€{intent.amount_cents / 100:.2f}"
    if not approved:
        intent.status = "denied"
        intent.decision_reason = "denied by user"
        await ledger.record(db, user_id, "spend_denied", actor_type="user",
                            entity_type="spend_intent", entity_id=intent.id,
                            payload={"reason": "denied by user"})
        return f"Denied: {amount} for {intent.purpose}."
    intent.status = "approved"
    await ledger.record(db, user_id, "spend_approved", actor_type="user",
                        entity_type="spend_intent", entity_id=intent.id,
                        payload={"mode": "human"})
    await execute_spend(db, intent)
    return f"Approved and executed: {amount} for {intent.purpose}."


approvals.EXECUTORS["spend"] = _spend_approval_executor


# --- Treasury simulator (§77): pure policy evaluation, no persistence, no money. ---

async def simulate(db: AsyncSession, user_id: str, cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        decision, reason = await evaluate(
            db, user_id,
            amount_cents=int(case.get("amount_cents", 0)),
            agent_id=case.get("agent_id"),
            category=case.get("category"),
            vendor=case.get("vendor"),
        )
        results.append({**case, "decision": decision, "reason": reason})
    return results
