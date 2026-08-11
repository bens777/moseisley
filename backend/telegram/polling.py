"""Long-polling mode for self-hosted deployments without a public webhook URL.

Run inside the worker process when TELEGRAM_MODE=polling.
"""
from __future__ import annotations

import asyncio
import logging

from backend.core.config import get_settings
from backend.core.db import get_sessionmaker
from backend.telegram.api import TelegramClient
from backend.telegram.gateway import Gateway

logger = logging.getLogger("mychief.telegram.polling")


async def run_polling_loop(stop_event: asyncio.Event | None = None) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.info("telegram polling disabled: no bot token")
        return
    client = TelegramClient(settings.telegram_bot_token)
    gateway = Gateway(client)
    offset: int | None = None
    while stop_event is None or not stop_event.is_set():
        try:
            updates = await client.get_updates(offset=offset, timeout=25)
            for update in updates:
                offset = update["update_id"] + 1
                async with get_sessionmaker()() as db:
                    await gateway.process_update(db, update)
        except Exception:
            logger.exception("telegram polling error; retrying in 5s")
            await asyncio.sleep(5)
