"""Proactive Manager nudges.

The Manager opens the conversation when the user's setup has an obvious hole,
instead of waiting to be asked. A nudge is a real, persisted assistant message
in the Manager session — same thread, same history — carrying a clickable
action so the next step is one tap away.

Three rules keep this from becoming noise:
  · ONE nudge kind is ever sent once — the kind is recorded on the user, so a
    dismissed or answered nudge never comes back;
  · at most one nudge is pending — while the last message in the thread is an
    unanswered nudge, no second one is posted;
  · a nudge only fires when its condition is genuinely true right now.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import actions
from backend.core.models import (
    AgentConfig,
    AgentSession,
    ChatMessage,
    Goal,
    Project,
    ScheduledJob,
    User,
)
from backend.ledger import service as ledger

logger = logging.getLogger("mychief.manager")

SENT_KEY = "manager_nudges_sent"   # user.settings_json: list of nudge kinds

NO_PROJECT = "no_project"
NO_GOAL = "no_goal"
NO_SCHEDULE = "no_schedule"

TEXTS: dict[str, str] = {
    NO_PROJECT: (
        "Welcome back. You don't have a project yet — a project gives your crew a "
        "mission. [Create your first project](action:projects) and I'll assemble the "
        "right agents around it. Or just tell me what you want to accomplish."
    ),
    NO_GOAL: (
        "Your crew is assembled but it has nothing to aim at. A goal is a metric, a "
        "target and a deadline — tell me what you want in plain words and I'll compile "
        "it, or [set one yourself](action:goals)."
    ),
    NO_SCHEDULE: (
        "You have a goal, but nothing runs on its own yet. Tell me what should happen "
        "regularly — \"watch my market every morning\", \"review my goals on Mondays\" — "
        "and I'll draft it. You can see everything that recurs on your "
        "[schedule](action:schedule)."
    ),
}


async def _counts(db: AsyncSession, user_id: str) -> dict[str, int]:
    async def count(model, *where) -> int:
        return int((await db.execute(
            select(func.count()).select_from(model).where(*where))).scalar() or 0)

    return {
        "projects": await count(Project, Project.user_id == user_id,
                                Project.status.in_(["active", "experiment", "hold"])),
        "goals": await count(Goal, Goal.user_id == user_id, Goal.status == "active"),
        "agents": await count(AgentConfig, AgentConfig.user_id == user_id,
                              AgentConfig.enabled.is_(True)),
        "schedules": await count(ScheduledJob, ScheduledJob.user_id == user_id,
                                 ScheduledJob.status == "scheduled"),
    }


def _applicable(counts: dict[str, int]) -> str | None:
    """The single most useful thing missing, in setup order."""
    if not counts["projects"] and not counts["goals"]:
        return NO_PROJECT
    if counts["agents"] and not counts["goals"]:
        return NO_GOAL
    if counts["goals"] and not counts["schedules"]:
        return NO_SCHEDULE
    return None


def sent_kinds(user: User) -> list[str]:
    raw = (user.settings_json or {}).get(SENT_KEY)
    return list(raw) if isinstance(raw, list) else []


async def _pending(db: AsyncSession, session_id: str) -> bool:
    """True when the newest message is a nudge the user hasn't replied to."""
    last = (await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(1)
    )).scalars().first()
    return last is not None and bool((last.metadata_json or {}).get("nudge"))


async def maybe_post(db: AsyncSession, user: User, session: AgentSession) -> ChatMessage | None:
    """Post at most one proactive message. Returns it, or None when nothing is due."""
    already = sent_kinds(user)
    if await _pending(db, session.id):
        return None
    kind = _applicable(await _counts(db, user.id))
    if kind is None or kind in already:
        return None

    message = ChatMessage(
        user_id=user.id, session_id=session.id, role="assistant",
        content=actions.sanitize(TEXTS[kind]), channel="web",
        metadata_json={"nudge": kind},
    )
    db.add(message)
    user.settings_json = {**(user.settings_json or {}), SENT_KEY: [*already, kind]}
    await db.flush()
    await ledger.record(db, user.id, "manager_nudge_sent", actor_type="agent",
                        actor_id="manager", payload={"kind": kind})
    return message
