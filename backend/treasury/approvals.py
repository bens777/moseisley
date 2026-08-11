"""Approval lifecycle (§79). Resolvable from dashboard or Telegram inline buttons.

Action execution on approval is dispatched through EXECUTORS — deterministic code
registered per action_type (spend → Treasury, gmail_send → Tool Broker, ...).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import ApprovalRequest
from backend.ledger import service as ledger

DEFAULT_TTL_HOURS = 72

# action_type -> async fn(db, user_id, approval, approved: bool) -> str (user-facing result)
EXECUTORS: dict[str, Callable[[AsyncSession, str, ApprovalRequest, bool], Awaitable[str]]] = {}


class ApprovalError(Exception):
    pass


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


async def create(
    db: AsyncSession, user_id: str, action_type: str, payload: dict, *,
    risk_level: int = 3, ttl_hours: int = DEFAULT_TTL_HOURS,
) -> ApprovalRequest:
    approval = ApprovalRequest(
        user_id=user_id, action_type=action_type, action_payload_json=payload,
        risk_level=risk_level,
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
    )
    db.add(approval)
    await db.flush()
    await ledger.record(db, user_id, "approval_requested", entity_type="approval",
                        entity_id=approval.id, payload={"action_type": action_type, **payload})
    return approval


async def get(db: AsyncSession, user_id: str, approval_id: str) -> ApprovalRequest | None:
    return (
        await db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user_id
            )
        )
    ).scalar_one_or_none()


async def list_pending(db: AsyncSession, user_id: str) -> list[ApprovalRequest]:
    now = datetime.now(UTC)
    rows = list(
        (await db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending"
            ).order_by(ApprovalRequest.created_at)
        )).scalars()
    )
    fresh = []
    for r in rows:
        if r.expires_at is not None and _as_utc(r.expires_at) < now:
            r.status = "expired"
            r.resolved_at = now
        else:
            fresh.append(r)
    await db.flush()
    return fresh


async def resolve(
    db: AsyncSession, user_id: str, approval_id: str, *, approve: bool, channel: str = "dashboard"
) -> str:
    approval = await get(db, user_id, approval_id)
    if approval is None:
        raise ApprovalError("approval not found")
    if approval.status != "pending":
        raise ApprovalError(f"approval already {approval.status}")
    now = datetime.now(UTC)
    if approval.expires_at is not None and _as_utc(approval.expires_at) < now:
        approval.status = "expired"
        approval.resolved_at = now
        await db.flush()
        raise ApprovalError("approval expired")

    approval.status = "approved" if approve else "denied"
    approval.resolved_at = now
    approval.resolution_channel = channel
    await ledger.record(db, user_id, "approval_resolved", actor_type="user",
                        entity_type="approval", entity_id=approval.id,
                        payload={"status": approval.status, "channel": channel})
    executor = EXECUTORS.get(approval.action_type)
    if executor is not None:
        result = await executor(db, user_id, approval, approve)
    else:
        result = f"Request {approval.status}."
    await db.flush()
    return result
