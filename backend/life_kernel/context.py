"""Context Loader (§19): assembles the sanitized context DTO handed to agents.

Never includes secrets, tokens, or raw credentials (§26).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import User
from backend.documents import service as documents
from backend.life_kernel import world_model


async def load_agent_context(db: AsyncSession, user: User) -> dict:
    focus = await documents.get_or_create(db, user.id, documents.FOCUS_PATH)
    ideal = await documents.get_document(db, user.id, documents.IDEAL_STATE_PATH)
    snapshot = await world_model.snapshot(db, user.id)
    return {
        "user": {"display_name": user.display_name, "timezone": user.timezone,
                 "autonomy_mode": user.autonomy_mode},
        "focus_md": focus.content_md,
        "ideal_state_md": ideal.content_md if ideal else None,
        "world": snapshot,
    }


def context_to_system_prompt(context: dict) -> str:
    parts = [
        "You are the user's Native Agent on Moseisley.sh, the command center where their AI crew works.",
        "Be concise, concrete, and honest about uncertainty. Use the user's actual goals and "
        "state below — never generic productivity advice.",
        f"\n## Focus\n{context['focus_md']}",
    ]
    if context.get("ideal_state_md"):
        parts.append(f"\n## Ideal State\n{context['ideal_state_md']}")
    w = context["world"]
    if w["goals"]:
        parts.append("\n## Current goals (structured)")
        for g in w["goals"]:
            parts.append(
                f"- {g['title']}: metric={g['metric']}, target={g['target']} {g['unit'] or ''}, "
                f"deadline={g['deadline']}, progress={g['progress']:.0%}"
            )
    if w["pending_approvals"]:
        parts.append(f"\nPending approvals waiting for the user: {w['pending_approvals']}")
    return "\n".join(parts)
