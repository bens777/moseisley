"""Telegram approval notifications with inline APPROVE/DENY buttons (§79, §98)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import ApprovalRequest, SpendIntent, TelegramBinding

logger = logging.getLogger("mychief.treasury.notify")


async def notify_spend_approval(db: AsyncSession, user_id: str,
                                approval: ApprovalRequest, intent: SpendIntent) -> bool:
    from backend.api.routes.telegram import get_gateway

    gateway = get_gateway()
    if gateway is None:
        return False
    binding = (await db.execute(select(TelegramBinding).where(
        TelegramBinding.user_id == user_id
    ))).scalar_one_or_none()
    if binding is None:
        return False
    text = (
        f"Your crew wants to spend €{intent.amount_cents / 100:.2f}.\n\n"
        f"Purpose:\n{intent.purpose}"
        + (f"\n\nVendor: {intent.vendor}" if intent.vendor else "")
    )
    markup = {"inline_keyboard": [[
        {"text": "APPROVE", "callback_data": f"appr:{approval.id}:approve"},
        {"text": "DENY", "callback_data": f"appr:{approval.id}:deny"},
    ]]}
    try:
        await gateway.client.send_message(binding.telegram_chat_id, text, reply_markup=markup)
        return True
    except Exception:
        logger.exception("failed to send telegram approval notification")
        return False
