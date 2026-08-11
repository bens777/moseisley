"""Native agent conversational reply (used by web chat and Telegram).

Goal-shaped messages are routed through the Goal Compiler; everything else gets a
context-aware reply. The full AgentAdapter registry arrives in Phase 9 — this module
is the native reasoning core it wraps.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import AgentSession, ChatMessage, User
from backend.life_kernel import goal_compiler

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_GOAL_HINT = re.compile(
    rf"\b(i want|my goal|goal is|reach|earn|achieve|increase .* to|by ({_MONTHS}|\d{{4}}))\b",
    re.IGNORECASE,
)

MAX_HISTORY = 20


async def get_or_create_default_session(db: AsyncSession, user_id: str) -> AgentSession:
    session = (
        await db.execute(
            select(AgentSession).where(AgentSession.user_id == user_id, AgentSession.is_default.is_(True))
        )
    ).scalar_one_or_none()
    if session is None:
        session = AgentSession(user_id=user_id, is_default=True, title="Main")
        db.add(session)
        await db.flush()
    return session


def _looks_like_goal(text: str) -> bool:
    return bool(_GOAL_HINT.search(text)) and any(ch.isdigit() for ch in text)


async def reply(
    db: AsyncSession, user: User, session: AgentSession, text: str, *, channel: str = "web"
) -> str:
    """Gateway into the Orchestrator (owner directive §20).

    A deterministic goal fast-path handles explicit goal statements (spec §6 prefers
    deterministic code where sufficient); everything else runs the bounded Orchestrator
    tool loop, which can itself invoke goals.create/memory/crew tools.
    """
    prior = await _pending_goal_extraction(db, session.id)
    if prior is not None or _looks_like_goal(text):
        db.add(ChatMessage(user_id=user.id, session_id=session.id, role="user",
                           content=text, channel=channel))
        await db.flush()
        result = await goal_compiler.compile_goal(db, user.id, text, prior_extracted=prior)
        if result.status == "created":
            g = result.goal
            target = f"{g.target_value:g} {g.unit or ''}".strip()
            answer = (
                f"Goal locked in: **{g.title}** — {g.metric} → {target}"
                + (f" by {g.deadline}" if g.deadline else "")
                + ". I've updated your Focus. I'll track progress and flag drift."
            )
            meta = {}
        elif result.status == "needs_clarification":
            answer = result.question or "Could you clarify the goal?"
            meta = {"pending_goal_extraction": result.extracted}
        else:
            answer = result.question or "I couldn't parse that goal — try rephrasing."
            meta = {}
        db.add(ChatMessage(user_id=user.id, session_id=session.id, role="assistant",
                           content=answer, channel=channel, metadata_json=meta))
        await db.flush()
        return answer

    from backend.agents import orchestrator

    return await orchestrator.handle_message(db, user, session, text, channel=channel)


async def _pending_goal_extraction(db: AsyncSession, session_id: str) -> dict | None:
    last_assistant = (
        await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if last_assistant is None:
        return None
    return (last_assistant.metadata_json or {}).get("pending_goal_extraction")
