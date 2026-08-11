"""Agent Router (§19): routes a user message through the active agent.

Moseisley.sh keeps identity, memory, goals, integrations, permissions and audit history
regardless of which agent runtime answers (§28). Adapters receive sanitized DTOs only.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import registry as agent_registry
from backend.agents.native import chat as native_chat
from backend.core.models import User

logger = logging.getLogger("mychief.agents")


async def route_message(db: AsyncSession, user: User, text: str, *, channel: str = "web") -> str:
    agent = await agent_registry.get_active(db, user.id)
    session = await native_chat.get_or_create_default_session(db, user.id)

    if agent.adapter_type == "native":
        return await native_chat.reply(db, user, session, text, channel=channel)

    # External adapters (custom_http/hermes/openclaw) — Phase 9 wires these.
    from backend.agents.adapters import get_adapter

    adapter = get_adapter(agent)
    if adapter is None:
        logger.warning("no adapter for %s; falling back to native", agent.adapter_type)
        return await native_chat.reply(db, user, session, text, channel=channel)
    return await adapter.relay_message(db, user, agent, session, text, channel=channel)
