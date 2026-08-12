"""AgentAdapter contract (§26) and shared relay logic.

Adapters receive sanitized DTOs only: no ORM objects, no provider secrets, no OAuth
tokens, no Telegram credentials. Moseisley.sh owns identity/memory/goals/policy (§28);
external agents are replaceable workers whose replies flow back through Moseisley.sh.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import inspection
from backend.agents.native import chat as native_chat
from backend.core.crypto import decrypt_secret
from backend.core.models import AgentConfig, AgentSession, ChatMessage, User
from backend.life_kernel.context import load_agent_context

logger = logging.getLogger("mychief.agents.adapters")


class AgentAdapterError(Exception):
    pass


class AgentAdapter:
    adapter_type = "base"

    async def health_check(self, agent: AgentConfig) -> bool:
        raise NotImplementedError

    async def capabilities(self, agent: AgentConfig) -> dict:
        return {"chat": True}

    async def send_message(
        self, agent: AgentConfig, user_id: str, session_id: str, message: str, context: dict
    ) -> str:
        """Return the agent's reply text. Must raise AgentAdapterError on failure."""
        raise NotImplementedError

    async def cancel(self, agent: AgentConfig, session_id: str) -> None:
        return None

    def _secret(self, agent: AgentConfig) -> str | None:
        if agent.encrypted_credentials:
            return decrypt_secret(agent.encrypted_credentials)
        return None

    async def relay_message(
        self, db: AsyncSession, user: User, agent: AgentConfig, session: AgentSession,
        text: str, *, channel: str = "web",
    ) -> str:
        """Shared pipeline: store user msg → sanitized context → adapter → store reply."""
        db.add(ChatMessage(user_id=user.id, session_id=session.id, role="user",
                           content=text, channel=channel, agent_id=agent.id))
        await db.flush()
        context = await load_agent_context(db, user)  # sanitized DTO — never secrets (§26)
        try:
            reply = await self.send_message(agent, user.id, session.id, text, context)
            agent.health_status = "ok"
        except Exception as e:  # noqa: BLE001 - degrade gracefully, never lose the channel
            logger.warning("agent %s failed: %s; falling back to native", agent.display_name, e)
            agent.health_status = "error"
            reply = await native_chat.reply(db, user, session, text, channel=channel)
            return reply

        # The reply crossed a trust boundary. Screen it BEFORE it is stored: this
        # session's history is what the tool-using native orchestrator reads next.
        outcome = await inspection.inspect(db, user, agent, reply)
        if not outcome.released:
            outcome.record.session_id = session.id   # where an approval would land it
            stored = inspection.notice_for(outcome, agent.display_name)
            db.add(ChatMessage(
                user_id=user.id, session_id=session.id, role="assistant",
                content=stored, channel=channel, agent_id=agent.id,
                metadata_json={"inspection_id": outcome.record.id,
                               "verdict": outcome.verdict, "withheld": True}))
            await db.flush()
            return stored

        db.add(ChatMessage(user_id=user.id, session_id=session.id, role="assistant",
                           content=outcome.text, channel=channel, agent_id=agent.id,
                           metadata_json={"inspection_id": outcome.record.id,
                                          "verdict": outcome.verdict}))
        await db.flush()
        return outcome.text


ADAPTER_TYPES: dict[str, type[AgentAdapter]] = {}


def register(cls: type[AgentAdapter]) -> type[AgentAdapter]:
    ADAPTER_TYPES[cls.adapter_type] = cls
    return cls


# populate registry
from backend.agents.adapters import custom_http, openclaw  # noqa: E402,F401
