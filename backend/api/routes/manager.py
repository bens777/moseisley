"""AI Manager API (third pass §11-§17): one persistent conversation, page
context, draft/save flow."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from backend.agents import manager as manager_svc
from backend.agents import orchestrator
from backend.core.models import ChatMessage
from backend.core.security import DB, CurrentUser

router = APIRouter(prefix="/manager")


class ManagerMessage(BaseModel):
    text: str
    page_context: dict | None = None


@router.get("/messages")
async def messages(user: CurrentUser, db: DB, limit: int = 50):
    session = await manager_svc.get_session(db, user)
    await db.commit()
    rows = list((await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc()).limit(min(limit, 200))
    )).scalars())[::-1]
    return [{"id": m.id, "role": m.role, "content": m.content,
             "created_at": m.created_at} for m in rows]


@router.post("/message")
async def send_message(body: ManagerMessage, user: CurrentUser, db: DB):
    session = await manager_svc.get_session(db, user)
    reply = await orchestrator.handle_message(
        db, user, session, body.text, channel="web", role="manager",
        page_context=body.page_context)
    draft = await manager_svc.get_draft(db, user.id)
    await db.commit()
    return {"reply": reply, "draft": draft or None}


@router.get("/draft")
async def get_draft(user: CurrentUser, db: DB):
    return {"draft": await manager_svc.get_draft(db, user.id)}


@router.post("/draft/apply")
async def apply_draft(user: CurrentUser, db: DB):
    result = await manager_svc.apply_draft(db, user)
    await db.commit()
    return result


@router.delete("/draft")
async def discard_draft(user: CurrentUser, db: DB):
    await manager_svc.discard_draft(db, user.id)
    await db.commit()
    return {"discarded": True}
