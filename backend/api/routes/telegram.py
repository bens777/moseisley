from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from backend.billing import entitlements
from backend.core.config import get_settings
from backend.core.models import TelegramBinding
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.telegram.api import TelegramClient
from backend.telegram.gateway import Gateway, create_pairing_code

router = APIRouter(prefix="/telegram")

_gateway: Gateway | None = None


def get_gateway() -> Gateway | None:
    global _gateway
    settings = get_settings()
    if _gateway is None and settings.telegram_bot_token:
        _gateway = Gateway(TelegramClient(settings.telegram_bot_token))
    return _gateway


def set_gateway(gateway: Gateway | None) -> None:
    """Test hook / polling-mode injection."""
    global _gateway
    _gateway = gateway


@router.post("/webhook")
async def webhook(request: Request, db: DB):
    settings = get_settings()
    if settings.telegram_webhook_secret:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if token != settings.telegram_webhook_secret:
            raise HTTPException(403, "bad webhook secret")
    gateway = get_gateway()
    if gateway is None:
        raise HTTPException(503, "telegram not configured")
    update = await request.json()
    await gateway.process_update(db, update)
    return {"ok": True}


@router.post("/pairing-code")
async def pairing_code(user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "telegram")
    code = await create_pairing_code(db, user.id)
    await db.commit()
    return {"code": code, "expires_in_minutes": 10, "instructions": f"Send /link {code} to the Moseisley.sh bot."}


@router.get("/binding")
async def get_binding(user: CurrentUser, db: DB):
    binding = (
        await db.execute(select(TelegramBinding).where(TelegramBinding.user_id == user.id))
    ).scalar_one_or_none()
    if binding is None:
        return {"linked": False}
    return {
        "linked": True,
        "linked_at": binding.linked_at,
        "voice_reply_mode": binding.voice_reply_mode,
    }


@router.delete("/binding")
async def unlink(user: CurrentUser, db: DB):
    binding = (
        await db.execute(select(TelegramBinding).where(TelegramBinding.user_id == user.id))
    ).scalar_one_or_none()
    if binding is None:
        raise HTTPException(404, "not linked")
    await db.delete(binding)
    await ledger.record(db, user.id, "telegram_unlinked", actor_type="user")
    await db.commit()
    return {"ok": True}
