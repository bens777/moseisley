from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.agents import registry as agent_registry
from backend.agents.adapters import get_adapter
from backend.core.crypto import encrypt_secret
from backend.core.models import AgentConfig
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger

router = APIRouter(prefix="/agents")

CREATABLE_TYPES = {"custom_http", "openclaw"}
# hermes: no stable request/reply HTTP API in the current hermes-agent gateway
# (channel-oriented only) — blocked; use custom_http as the escape hatch (§27).


def _serialize(a: AgentConfig) -> dict:
    return {
        "id": a.id, "adapter_type": a.adapter_type, "display_name": a.display_name,
        "enabled": a.enabled, "is_active": a.is_active, "health_status": a.health_status,
        "configuration": a.configuration_json, "capabilities": a.capabilities_json,
        "has_credentials": bool(a.encrypted_credentials), "created_at": a.created_at,
    }


@router.get("")
async def list_agents(user: CurrentUser, db: DB):
    agents = await agent_registry.list_agents(db, user.id)
    await db.commit()
    return [_serialize(a) for a in agents]


class CreateAgentRequest(BaseModel):
    adapter_type: str
    display_name: str
    configuration: dict = {}
    credential: str | None = None  # auth header value / gateway token


@router.post("")
async def create_agent(body: CreateAgentRequest, user: CurrentUser, db: DB):
    if body.adapter_type not in CREATABLE_TYPES:
        raise HTTPException(400, f"adapter type not available: {body.adapter_type}. "
                                 "Hermes has no stable HTTP API yet — use custom_http.")
    agent = AgentConfig(
        user_id=user.id, adapter_type=body.adapter_type, display_name=body.display_name,
        configuration_json=body.configuration,
        encrypted_credentials=encrypt_secret(body.credential) if body.credential else None,
    )
    db.add(agent)
    await db.flush()
    await ledger.record(db, user.id, "agent_switched", actor_type="user", entity_type="agent",
                        entity_id=agent.id, payload={"event": "agent_added",
                                                     "adapter_type": body.adapter_type})
    await db.commit()
    return _serialize(agent)


@router.post("/{agent_id}/activate")
async def activate(agent_id: str, user: CurrentUser, db: DB):
    try:
        agent = await agent_registry.set_active(db, user.id, agent_id)
    except agent_registry.AgentNotFound as e:
        raise HTTPException(404, "agent not found or disabled") from e
    await db.commit()
    return _serialize(agent)


@router.post("/{agent_id}/health")
async def health(agent_id: str, user: CurrentUser, db: DB):
    agent = (await db.execute(select(AgentConfig).where(
        AgentConfig.id == agent_id, AgentConfig.user_id == user.id
    ))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "agent not found")
    if agent.adapter_type == "native":
        ok = True
    else:
        adapter = get_adapter(agent)
        ok = await adapter.health_check(agent) if adapter else False
    agent.health_status = "ok" if ok else "error"
    await db.commit()
    return {"ok": ok}


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, user: CurrentUser, db: DB):
    agent = (await db.execute(select(AgentConfig).where(
        AgentConfig.id == agent_id, AgentConfig.user_id == user.id
    ))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "agent not found")
    if agent.adapter_type == "native":
        raise HTTPException(400, "the native agent cannot be removed")
    was_active = agent.is_active
    await db.delete(agent)
    if was_active:
        await db.flush()
        await agent_registry.get_active(db, user.id)  # falls back to native
    await db.commit()
    return {"ok": True}
