"""The AI Manager (third pass §11-§17).

One persistent Manager conversation per user (AgentSession channel='manager'),
routed through the same bounded orchestrator loop with the Manager prompt and
page context. Drafts are the SAVE-UX boundary: the LLM can only stage a
structured draft; persistence happens through deterministic apply — either the
user clicking SAVE in the UI or explicitly confirming in conversation (which
makes the model call instructions.save; the confirmation message is in the
stored transcript).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import AgentSession, SystemSetting, User
from backend.ledger import service as ledger
from backend.ops import instructions as instructions_svc

DRAFT_KEY = "manager_draft"


async def get_session(db: AsyncSession, user: User) -> AgentSession:
    row = (await db.execute(select(AgentSession).where(
        AgentSession.user_id == user.id, AgentSession.channel == "manager"
    ))).scalars().first()
    if row is None:
        row = AgentSession(user_id=user.id, agent_id=None, title="Manager",
                           channel="manager")
        db.add(row)
        await db.flush()
    return row


async def _setting(db: AsyncSession, user_id: str) -> SystemSetting | None:
    return (await db.execute(select(SystemSetting).where(
        SystemSetting.user_id == user_id, SystemSetting.key == DRAFT_KEY
    ))).scalar_one_or_none()


async def get_draft(db: AsyncSession, user_id: str) -> dict | None:
    row = await _setting(db, user_id)
    return row.value_json if row and row.value_json else None


async def store_draft(db: AsyncSession, user: User, args) -> dict:
    """Validate and stage a draft (LLM tool instructions.draft). Nothing is saved."""
    try:
        instructions_svc._validate(args.name, args.kind, args.schedule or {})
    except instructions_svc.InstructionError as e:
        return {"error": str(e)}
    draft = {
        "name": args.name, "kind": args.kind, "config": args.config,
        "schedule": args.schedule, "delivery": args.delivery,
        "assigned_role": args.assigned_role, "project_id": args.project_id,
        "instruction_id": args.instruction_id,
        "created_at": datetime.now(UTC).isoformat(),
    }
    row = await _setting(db, user.id)
    if row is None:
        row = SystemSetting(user_id=user.id, key=DRAFT_KEY, value_json=draft)
        db.add(row)
    else:
        row.value_json = draft
    await db.flush()
    await ledger.record(db, user.id, "manager_draft_created", actor_type="agent",
                        actor_id="manager", payload={"name": draft["name"],
                                                     "kind": draft["kind"]})
    return {"draft": draft,
            "note": "Draft staged — NOT saved. The user must confirm before it is applied."}


async def apply_draft(db: AsyncSession, user: User) -> dict:
    """Deterministically persist the staged draft (SAVE button or explicit
    conversational confirmation)."""
    draft = await get_draft(db, user.id)
    if not draft:
        return {"error": "no draft staged"}
    try:
        if draft.get("instruction_id"):
            instruction = await instructions_svc.update(
                db, user, draft["instruction_id"], changed_by="manager",
                reason="manager conversation", name=draft["name"], kind=draft["kind"],
                config=draft.get("config") or {}, schedule=draft.get("schedule") or {},
                delivery=draft.get("delivery") or [],
                assigned_role=draft.get("assigned_role"))
        else:
            instruction = await instructions_svc.create(
                db, user, name=draft["name"], kind=draft["kind"],
                config=draft.get("config") or {}, schedule=draft.get("schedule") or {},
                delivery=draft.get("delivery") or [],
                assigned_role=draft.get("assigned_role"),
                project_id=draft.get("project_id"),
                created_by="manager", reason="manager conversation")
    except instructions_svc.InstructionError as e:
        return {"error": str(e)}
    row = await _setting(db, user.id)
    if row is not None:
        row.value_json = {}
        await db.flush()
    await ledger.record(db, user.id, "manager_draft_saved", actor_type="agent",
                        actor_id="manager", entity_type="instruction",
                        entity_id=instruction.id, payload={"name": instruction.name})
    return {"saved": True, "instruction_id": instruction.id,
            "instruction": json.loads(json.dumps(
                instructions_svc.serialize(instruction), default=str))}


async def discard_draft(db: AsyncSession, user_id: str) -> None:
    row = await _setting(db, user_id)
    if row is not None:
        row.value_json = {}
        await db.flush()
