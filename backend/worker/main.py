"""Moseisley.sh worker: scheduled jobs + optional Telegram long-polling.

Run: python -m backend.worker.main
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

import backend.jobs.handlers  # noqa: F401 - registers job handlers
import backend.market.radar  # noqa: F401 - registers market handlers
from backend.core.config import get_settings
from backend.core.db import get_sessionmaker
from backend.core.logging import setup_logging
from backend.jobs import scheduler

logger = logging.getLogger("mychief.worker")


async def job_loop(worker_id: str, stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            async with get_sessionmaker()() as db:
                ran = await scheduler.tick(db, worker_id)
            if ran:
                logger.info("executed %d job(s)", ran)
        except Exception:
            logger.exception("worker tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            pass


async def main() -> None:
    setup_logging()
    settings = get_settings()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(job_loop(worker_id, stop_event))]
    if settings.telegram_mode == "polling" and settings.telegram_bot_token:
        from backend.telegram.polling import run_polling_loop

        tasks.append(asyncio.create_task(run_polling_loop(stop_event)))
    logger.info("mychief worker started (%s)", worker_id)
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
