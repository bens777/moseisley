from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from backend.agents.native import chat as native_chat
from backend.core import killswitch
from backend.core.models import ChatMessage
from backend.core.security import DB, CurrentUser

router = APIRouter(prefix="/chat")


@router.get("/messages")
async def list_messages(user: CurrentUser, db: DB, limit: int = Query(default=50, le=200)):
    session = await native_chat.get_or_create_default_session(db, user.id)
    await db.commit()
    messages = list((await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc()).limit(limit)
    )).scalars())[::-1]
    return [
        {"id": m.id, "role": m.role, "content": m.content, "channel": m.channel,
         "created_at": m.created_at}
        for m in messages
    ]


class SendMessageRequest(BaseModel):
    text: str


@router.post("/message")
async def send_message(body: SendMessageRequest, user: CurrentUser, db: DB):
    await killswitch.require_off(db, user.id, killswitch.PAUSE_ALL_AGENTS)
    session = await native_chat.get_or_create_default_session(db, user.id)
    answer = await native_chat.reply(db, user, session, body.text, channel="web")
    await db.commit()
    return {"reply": answer}
