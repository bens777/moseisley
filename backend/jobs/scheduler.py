"""Scheduler (§85): scheduled_jobs table + polling worker + optimistic locking.

Jobs are idempotent (idempotency_key), retryable (attempts/backoff) and
concurrency-safe (atomic claim via conditional UPDATE — works on PG and SQLite;
on PG this is equivalent in effect to advisory-lock protection).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import ScheduledJob

logger = logging.getLogger("mychief.jobs")

# job_type -> async handler(db, job) -> dict (result payload)
HANDLERS: dict[str, Callable[[AsyncSession, ScheduledJob], Awaitable[dict]]] = {}

RETRY_BACKOFF_SECONDS = 300


def handler(job_type: str):
    def deco(fn):
        HANDLERS[job_type] = fn
        return fn
    return deco


async def enqueue(
    db: AsyncSession,
    job_type: str,
    *,
    user_id: str | None = None,
    payload: dict | None = None,
    run_at: datetime | None = None,
    interval_seconds: int | None = None,
    cron_hint: str | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> ScheduledJob | None:
    if idempotency_key:
        existing = (
            await db.execute(select(ScheduledJob).where(
                ScheduledJob.idempotency_key == idempotency_key,
                ScheduledJob.status.in_(["scheduled", "running"]),
            ))
        ).scalars().first()
        if existing is not None:
            return None
    job = ScheduledJob(
        user_id=user_id, job_type=job_type, payload_json=payload or {},
        next_run_at=run_at or datetime.now(UTC), interval_seconds=interval_seconds,
        cron_hint=cron_hint, idempotency_key=idempotency_key, max_attempts=max_attempts,
    )
    db.add(job)
    await db.flush()
    return job


async def claim_one(db: AsyncSession, worker_id: str) -> ScheduledJob | None:
    """Atomically claim the next due job. Safe under concurrent workers."""
    now = datetime.now(UTC)
    candidates = (
        await db.execute(
            select(ScheduledJob.id).where(
                ScheduledJob.status == "scheduled", ScheduledJob.next_run_at <= now
            ).order_by(ScheduledJob.next_run_at).limit(5)
        )
    ).scalars().all()
    for job_id in candidates:
        result = await db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id, ScheduledJob.status == "scheduled")
            .values(status="running", locked_at=now, locked_by=worker_id)
        )
        if result.rowcount == 1:
            await db.commit()
            return (await db.execute(select(ScheduledJob).where(ScheduledJob.id == job_id))).scalar_one()
        await db.rollback()
    return None


async def run_job(db: AsyncSession, job: ScheduledJob) -> None:
    now = datetime.now(UTC)
    fn = HANDLERS.get(job.job_type)
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job type {job.job_type}")
        result = await fn(db, job)
        job.last_run_at = now
        job.last_error = None
        job.attempts = 0
        if job.interval_seconds:
            job.status = "scheduled"
            job.next_run_at = now + timedelta(seconds=job.interval_seconds)
        else:
            job.status = "done"
        job.payload_json = {**job.payload_json, "last_result": result}
    except Exception as e:  # noqa: BLE001 - job errors are recorded, not raised
        logger.exception("job %s (%s) failed", job.id, job.job_type)
        job.attempts += 1
        job.last_error = f"{type(e).__name__}: {e}"
        job.last_run_at = now
        if job.attempts >= job.max_attempts and not job.interval_seconds:
            job.status = "failed"
        else:
            job.status = "scheduled"
            job.next_run_at = now + timedelta(
                seconds=job.interval_seconds or RETRY_BACKOFF_SECONDS * job.attempts
            )
    finally:
        job.locked_at = None
        job.locked_by = None
        await db.commit()


async def tick(db: AsyncSession, worker_id: str = "worker-1", max_jobs: int = 10) -> int:
    """Claim and run due jobs. Returns number of jobs executed."""
    count = 0
    for _ in range(max_jobs):
        job = await claim_one(db, worker_id)
        if job is None:
            break
        await run_job(db, job)
        count += 1
    return count


def next_local_time(tz_name: str, hour: int, minute: int = 0, *, weekday: int | None = None,
                    day_of_month: int | None = None) -> datetime:
    """Next occurrence of a local time in the user's timezone, as UTC (§86)."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day_of_month is not None:
        if candidate.day != day_of_month or candidate <= now:
            month, year = (now.month % 12) + 1, now.year + (1 if now.month == 12 else 0)
            if now.day < day_of_month:
                month, year = now.month, now.year
            candidate = candidate.replace(year=year, month=month, day=day_of_month)
    elif weekday is not None:
        days_ahead = (weekday - candidate.weekday()) % 7
        if days_ahead == 0 and candidate <= now:
            days_ahead = 7
        candidate += timedelta(days=days_ahead)
    elif candidate <= now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


async def ensure_default_schedules(db: AsyncSession, user_id: str, tz_name: str) -> None:
    """Default always-on schedules (§86). Idempotent per user."""
    defaults = [
        ("market_radar", 6, 0, 86400, "daily 06:00"),
        ("daily_strategist", 8, 0, 86400, "daily 08:00"),
    ]
    for job_type, hour, minute, interval, hint in defaults:
        await enqueue(
            db, job_type, user_id=user_id,
            run_at=next_local_time(tz_name, hour, minute),
            interval_seconds=interval, cron_hint=hint,
            idempotency_key=f"{job_type}:{user_id}",
        )
    await enqueue(
        db, "weekly_review", user_id=user_id,
        run_at=next_local_time(tz_name, 8, 30, weekday=0),
        interval_seconds=7 * 86400, cron_hint="monday 08:30",
        idempotency_key=f"weekly_review:{user_id}",
    )
