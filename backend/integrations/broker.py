"""Tool Broker (§34): the single gate between agents and integrations.

Agents request semantic capabilities; the broker enforces kill switches and the
deterministic Policy Engine, dispatches to the owning adapter, and writes Ledger
events. Agents never receive integration credentials (§28).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch
from backend.core.models import IntegrationConnection
from backend.integrations.base import IntegrationAdapter, IntegrationError
from backend.integrations.demo import DemoAdapter
from backend.integrations.google.adapter import GoogleAdapter
from backend.integrations.mcp.client import McpAdapter
from backend.integrations.storage_adapter import S3ByosAdapter
from backend.integrations.webhook.adapter import N8nAdapter, RestAdapter, WebhookAdapter
from backend.ledger import service as ledger
from backend.policies import engine as policy

logger = logging.getLogger("mychief.broker")

ADAPTERS: dict[str, type[IntegrationAdapter]] = {
    "google": GoogleAdapter,
    "mcp": McpAdapter,
    "webhook": WebhookAdapter,
    "rest": RestAdapter,
    "n8n": N8nAdapter,
    "demo": DemoAdapter,
    "s3": S3ByosAdapter,
}


class BrokerError(Exception):
    pass


def build_adapter(connection: IntegrationConnection) -> IntegrationAdapter:
    cls = ADAPTERS.get(connection.integration_type)
    if cls is None:
        raise BrokerError(f"no adapter for integration type {connection.integration_type}")
    return cls(connection)


async def list_connections(db: AsyncSession, user_id: str) -> list[IntegrationConnection]:
    return list(
        (await db.execute(
            select(IntegrationConnection).where(IntegrationConnection.user_id == user_id)
            .order_by(IntegrationConnection.created_at)
        )).scalars()
    )


async def find_connection_for_capability(
    db: AsyncSession, user_id: str, capability: str, connection_id: str | None = None
) -> IntegrationConnection | None:
    for conn in await list_connections(db, user_id):
        if conn.status != "connected":
            continue
        if connection_id and conn.id != connection_id:
            continue
        if capability in build_adapter(conn).capabilities():
            return conn
    return None


async def invoke(
    db: AsyncSession,
    user_id: str,
    capability: str,
    operation: str,
    params: dict | None = None,
    *,
    actor_type: str = "agent",
    actor_id: str | None = None,
    connection_id: str | None = None,
) -> dict:
    """Invoke a semantic capability. Raises PolicyDenied / KillSwitchEngaged / BrokerError."""
    params = params or {}
    required = policy.CAPABILITY_REQUIREMENTS.get(capability)
    if required is None:
        raise BrokerError(f"unknown capability: {capability}")

    await ledger.record(db, user_id, "tool_requested", actor_type=actor_type, actor_id=actor_id,
                        payload={"capability": capability, "operation": operation})
    await killswitch.require_operational(db, user_id, killswitch.PAUSE_ALL_AGENTS)

    conn = await find_connection_for_capability(db, user_id, capability, connection_id)
    if conn is None:
        await ledger.record(db, user_id, "tool_denied", actor_type=actor_type, actor_id=actor_id,
                            payload={"capability": capability, "reason": "no_connection"})
        raise BrokerError(f"no connected integration provides {capability}")

    try:
        await policy.check(db, user_id, capability, conn.capabilities_json or {})
    except (policy.PolicyDenied, killswitch.KillSwitchEngaged) as e:
        await ledger.record(db, user_id, "tool_denied", actor_type=actor_type, actor_id=actor_id,
                            payload={"capability": capability, "reason": str(e)})
        raise

    adapter = build_adapter(conn)
    is_execute = policy.CAPABILITY_REQUIREMENTS[capability] == "EXECUTE"
    try:
        if is_execute:
            result = await adapter.execute(operation, params)
        else:
            result = await adapter.read(operation, params)
        status = "SUCCESS"
    except IntegrationError as e:
        status = "FAILED"
        await ledger.record(db, user_id, "tool_executed", actor_type=actor_type, actor_id=actor_id,
                            entity_type="integration", entity_id=conn.id,
                            payload={"capability": capability, "operation": operation, "status": status,
                                     "error": str(e)})
        raise
    # §112: status recorded truthfully; UNKNOWN propagated as-is from adapters.
    if isinstance(result, dict) and result.get("status") in ("FAILED", "UNKNOWN"):
        status = result["status"]
    await ledger.record(db, user_id, "tool_executed", actor_type=actor_type, actor_id=actor_id,
                        entity_type="integration", entity_id=conn.id,
                        payload={"capability": capability, "operation": operation, "status": status})
    return result
