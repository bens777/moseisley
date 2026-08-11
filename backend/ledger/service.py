"""Append-only Ledger service (§17).

All meaningful state changes call `record(...)`. Events are immutable: the ORM model
forbids update/delete, and no API mutates historical rows.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Event

EVENT_TYPES = {
    "goal_created", "goal_updated", "strategy_proposed", "strategy_changed",
    "market_scan_started", "market_scan_completed", "opportunity_detected",
    "experiment_created", "experiment_started", "experiment_stopped",
    "prediction_created", "outcome_recorded", "agent_switched",
    "tool_requested", "tool_executed", "tool_denied",
    "spend_requested", "spend_approved", "spend_denied", "spend_executed",
    "approval_requested", "approval_resolved",
    "provider_enabled", "provider_disabled", "provider_connected", "provider_removed",
    "integration_connected", "integration_disconnected",
    "system_paused", "system_resumed", "kill_switch_changed",
    "telegram_linked", "telegram_unlinked",
    "xray_started", "xray_completed", "audit_completed",
    "document_updated", "decision_recorded", "autopilot_draft_created",
    "project_created", "project_updated",
    "data_purged",
    "memory_created", "memory_updated", "memory_archived",
    "orchestrator_model_changed", "crew_model_changed", "prompt_changed",
    "crew_run_started", "crew_run_completed",
    "system_emergency_stopped", "subscription_changed",
    # third pass (2026-08-11): revenue, instructions, market watches, dev agent, manager
    "revenue_recorded", "revenue_reversed",
    "instruction_created", "instruction_updated", "instruction_toggled",
    "instruction_deleted", "instruction_run_completed",
    "market_report_created", "market_brief_delivered",
    "dev_proposal_created", "dev_patch_ready", "dev_proposal_approved",
    "dev_proposal_rejected", "dev_proposal_merged",
    "manager_draft_created", "manager_draft_saved",
}


async def record(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    *,
    actor_type: str = "system",
    actor_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
) -> Event:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown ledger event type: {event_type}")
    ev = Event(
        user_id=user_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload or {},
    )
    db.add(ev)
    await db.flush()
    return ev


async def list_events(
    db: AsyncSession,
    user_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    event_types: list[str] | None = None,
) -> list[Event]:
    q = select(Event).where(Event.user_id == user_id)
    if event_types:
        q = q.where(Event.event_type.in_(event_types))
    q = q.order_by(Event.created_at.desc(), Event.id).limit(limit).offset(offset)
    return list((await db.execute(q)).scalars())
