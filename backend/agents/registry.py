"""Agent registry (§25): replaceable agent runtimes, platform-owned governance.

Every user gets a default Native agent. Adapters are wired in backend/agents/adapters.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import AgentConfig
from backend.ledger import service as ledger


class AgentNotFound(Exception):
    pass


_DEFAULT_NATIVE_NAMES = ("Native Agent", "MyChief Native")


async def ensure_default_agents(db: AsyncSession, user_id: str) -> None:
    agents = list(
        (await db.execute(
            select(AgentConfig).where(AgentConfig.user_id == user_id)
            .order_by(AgentConfig.created_at, AgentConfig.id)
        )).scalars()
    )
    if not agents:
        db.add(AgentConfig(
            user_id=user_id, adapter_type="native", display_name="Native Agent",
            enabled=True, is_active=True, health_status="ok",
        ))
        await db.flush()
        return
    # Self-heal duplicate system defaults: two concurrent first requests can both pass
    # the emptiness check above and insert. Collapse unmodified default natives to one.
    dupes = [a for a in agents
             if a.adapter_type == "native" and a.display_name in _DEFAULT_NATIVE_NAMES
             and not a.encrypted_credentials]
    if len(dupes) > 1:
        keep, extras = dupes[0], dupes[1:]
        if any(d.is_active for d in extras) and not any(
            a.is_active for a in agents if a not in extras
        ):
            keep.is_active = True
        for d in extras:
            await db.delete(d)
        await db.flush()


async def list_agents(db: AsyncSession, user_id: str) -> list[AgentConfig]:
    await ensure_default_agents(db, user_id)
    return list(
        (await db.execute(
            select(AgentConfig).where(AgentConfig.user_id == user_id).order_by(AgentConfig.created_at)
        )).scalars()
    )


async def get_active(db: AsyncSession, user_id: str) -> AgentConfig:
    await ensure_default_agents(db, user_id)
    active = (
        await db.execute(
            select(AgentConfig).where(AgentConfig.user_id == user_id, AgentConfig.is_active.is_(True))
        )
    ).scalars().first()
    if active is None:
        agents = await list_agents(db, user_id)
        active = agents[0]
        active.is_active = True
        await db.flush()
    return active


async def set_active(db: AsyncSession, user_id: str, agent_id: str) -> AgentConfig:
    agents = await list_agents(db, user_id)
    target = next((a for a in agents if a.id == agent_id), None)
    if target is None or not target.enabled:
        raise AgentNotFound(agent_id)
    for a in agents:
        a.is_active = a.id == target.id
    await ledger.record(db, user_id, "agent_switched", actor_type="user",
                        entity_type="agent", entity_id=target.id,
                        payload={"display_name": target.display_name, "adapter_type": target.adapter_type})
    await db.flush()
    return target


async def set_active_by_name(db: AsyncSession, user_id: str, name: str) -> AgentConfig:
    name_lower = name.lower()
    for a in await list_agents(db, user_id):
        if name_lower in (a.display_name.lower(), a.adapter_type.lower()):
            return await set_active(db, user_id, a.id)
    raise AgentNotFound(name)
