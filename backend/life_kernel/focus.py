"""Focus document maintenance (§13). Deterministic — no LLM needed."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Goal, Project
from backend.documents import service as documents


async def rebuild_focus(db: AsyncSession, user_id: str) -> str:
    goals = list(
        (await db.execute(
            select(Goal).where(Goal.user_id == user_id, Goal.status == "active").order_by(Goal.created_at)
        )).scalars()
    )
    projects = list(
        (await db.execute(
            select(Project).where(Project.user_id == user_id, Project.status.in_(["active", "experiment"]))
            .order_by(Project.created_at)
        )).scalars()
    )
    lines = ["# Focus", ""]
    if goals:
        lines.append("## Active goals")
        for g in goals:
            target = f" → {g.target_value:g} {g.unit or ''}".rstrip() if g.target_value is not None else ""
            deadline = f" (by {g.deadline})" if g.deadline else ""
            lines.append(f"- {g.title}{target}{deadline}")
            for k, v in (g.constraints_json or {}).items():
                lines.append(f"  - constraint: {k} = {v}")
        lines.append("")
    if projects:
        lines.append("## Active projects")
        for p in projects:
            lines.append(f"- {p.name} [{p.status}]")
        lines.append("")
    if not goals and not projects:
        lines.append("_No active goals yet. Tell your crew what you want._")
    content = "\n".join(lines).rstrip() + "\n"
    await documents.upsert_document(db, user_id, documents.FOCUS_PATH, content, actor_type="system")
    return content
