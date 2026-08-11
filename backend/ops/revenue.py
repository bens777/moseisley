"""Canonical verified revenue (third pass §4-§8).

METHODOLOGY (inspectable, referenced by the UI):

- A RevenueEvent exists only for money actually verified from a connected
  source, or explicitly declared by the user (source='manual', labeled MANUAL
  in every UI). Opportunities, pipeline, and unpaid invoices never become
  RevenueEvents (they belong to X-Ray as ESTIMATED findings).
- VERIFIED REVENUE (period) = sum of non-reversed events with occurred_at in
  the period, per currency. No FX mixing: aggregates are per-currency; any
  converted total a UI shows must be labeled ESTIMATED FX.
- VERIFIED MRR = for events flagged recurring with interval 'monthly': the
  most recent non-reversed event per (project_id, source, source_ref) whose
  occurred_at falls within the last 35 days. Each recurring source counts
  once; lapsed subscriptions (no charge in 35 days) drop out. This is the
  lowest defensible value: it never extrapolates, it only counts charges that
  actually happened recently. Yearly/weekly recurrence is NOT normalized into
  MRR (counting less, not more).
- Reversals: a refund is recorded as verification_status='reversed' on the
  original event (or a reversal event pointing at it); reversed events are
  excluded from every aggregate.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import RevenueEvent
from backend.ledger import service as ledger

MRR_LOOKBACK_DAYS = 35

VALID_SOURCES = ("stripe", "payment_provider", "platform_api", "affiliate",
                 "marketplace", "manual")


async def record_event(
    db: AsyncSession,
    user_id: str,
    *,
    source: str,
    amount_cents: int,
    currency: str = "EUR",
    occurred_at: datetime | None = None,
    project_id: str | None = None,
    source_ref: str | None = None,
    description: str = "",
    recurring: bool = False,
    recurrence_interval: str | None = None,
    evidence: dict | None = None,
    actor_type: str = "user",
) -> RevenueEvent:
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid revenue source: {source}")
    if amount_cents <= 0:
        raise ValueError("revenue amount must be positive; record refunds as reversals")
    if recurring and recurrence_interval not in ("monthly", "yearly", "weekly"):
        raise ValueError("recurring events need recurrence_interval monthly|yearly|weekly")
    event = RevenueEvent(
        user_id=user_id,
        project_id=project_id,
        source=source,
        source_ref=source_ref,
        description=description[:500],
        amount_cents=amount_cents,
        currency=currency.upper()[:3],
        occurred_at=occurred_at or datetime.now(UTC),
        recurring=recurring,
        recurrence_interval=recurrence_interval if recurring else None,
        evidence_json=evidence or {},
        last_synced_at=datetime.now(UTC) if source != "manual" else None,
    )
    db.add(event)
    await db.flush()
    await ledger.record(db, user_id, "revenue_recorded", actor_type=actor_type,
                        entity_type="revenue_event", entity_id=event.id,
                        payload={"source": source, "amount_cents": amount_cents,
                                 "currency": event.currency, "recurring": recurring,
                                 "project_id": project_id})
    return event


async def reverse_event(db: AsyncSession, user_id: str, event_id: str,
                        *, reason: str = "", actor_type: str = "user") -> RevenueEvent:
    event = (await db.execute(select(RevenueEvent).where(
        RevenueEvent.id == event_id, RevenueEvent.user_id == user_id
    ))).scalar_one_or_none()
    if event is None:
        raise ValueError("revenue event not found")
    event.verification_status = "reversed"
    await db.flush()
    await ledger.record(db, user_id, "revenue_reversed", actor_type=actor_type,
                        entity_type="revenue_event", entity_id=event.id,
                        payload={"reason": reason})
    return event


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; canonical storage is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _events(db: AsyncSession, user_id: str,
                  project_id: str | None = None) -> list[RevenueEvent]:
    q = select(RevenueEvent).where(RevenueEvent.user_id == user_id,
                                   RevenueEvent.verification_status != "reversed")
    if project_id is not None:
        q = q.where(RevenueEvent.project_id == project_id)
    return list((await db.execute(q)).scalars())


async def verified_revenue(db: AsyncSession, user_id: str, *, days: int = 30,
                           project_id: str | None = None) -> dict[str, int]:
    """Per-currency verified revenue for the trailing period. Never mixes FX."""
    since = datetime.now(UTC) - timedelta(days=days)
    totals: dict[str, int] = {}
    for e in await _events(db, user_id, project_id):
        if _aware(e.occurred_at) >= since:
            totals[e.currency] = totals.get(e.currency, 0) + e.amount_cents
    return totals


async def verified_mrr(db: AsyncSession, user_id: str,
                       project_id: str | None = None) -> dict[str, int]:
    """Per-currency verified MRR per the module methodology (see docstring)."""
    since = datetime.now(UTC) - timedelta(days=MRR_LOOKBACK_DAYS)
    latest: dict[tuple, RevenueEvent] = {}
    for e in await _events(db, user_id, project_id):
        if not e.recurring or e.recurrence_interval != "monthly":
            continue
        if _aware(e.occurred_at) < since:
            continue
        key = (e.project_id, e.source, e.source_ref or e.id)
        prev = latest.get(key)
        if prev is None or _aware(e.occurred_at) > _aware(prev.occurred_at):
            latest[key] = e
    totals: dict[str, int] = {}
    for e in latest.values():
        totals[e.currency] = totals.get(e.currency, 0) + e.amount_cents
    return totals


async def list_events(db: AsyncSession, user_id: str, *, project_id: str | None = None,
                      limit: int = 100) -> list[RevenueEvent]:
    q = (select(RevenueEvent).where(RevenueEvent.user_id == user_id)
         .order_by(RevenueEvent.occurred_at.desc()).limit(limit))
    if project_id is not None:
        q = q.where(RevenueEvent.project_id == project_id)
    return list((await db.execute(q)).scalars())
