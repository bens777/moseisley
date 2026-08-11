from __future__ import annotations

from fastapi import APIRouter, Query

from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger

router = APIRouter(prefix="/activity")

FILTER_GROUPS = {
    "money": ["spend_requested", "spend_approved", "spend_denied", "spend_executed"],
    "agents": ["agent_switched", "tool_requested", "tool_executed", "tool_denied"],
    "market": ["market_scan_started", "market_scan_completed", "opportunity_detected"],
    "goals": ["goal_created", "goal_updated", "prediction_created", "outcome_recorded"],
    "integrations": [
        "integration_connected", "integration_disconnected",
        "provider_enabled", "provider_disabled", "provider_connected", "provider_removed",
    ],
    "actions": ["approval_requested", "approval_resolved", "autopilot_draft_created", "xray_completed"],
}


@router.get("")
async def list_activity(
    user: CurrentUser,
    db: DB,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    filter: str | None = None,
):
    types = FILTER_GROUPS.get(filter) if filter and filter != "all" else None
    events = await ledger.list_events(db, user.id, limit=limit, offset=offset, event_types=types)
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "payload": e.payload_json,
            "created_at": e.created_at,
        }
        for e in events
    ]
