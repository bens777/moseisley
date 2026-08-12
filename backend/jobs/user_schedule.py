"""The user's view of scheduled_jobs: one readable truth about what recurs.

The scheduler owns execution; this module owns explanation and the two edits a
user can make from the Schedule page — enable/disable and cadence. Both write
through paths that already exist:

  · instruction-backed jobs (job_type "instruction_run") are owned by their
    Instruction, so both edits delegate to backend.ops.instructions, which
    re-syncs scheduled_jobs deterministically;
  · built-in jobs (Radar, Strategist, weekly review) have no owning record, so
    they are edited in place using the scheduler's own next_local_time helper.

Disabling a built-in also records the choice on the user, because
ensure_default_schedules runs on every Command Center load and would otherwise
re-create the job the user just switched off.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Instruction, ScheduledJob, User
from backend.jobs import scheduler
from backend.ops import instructions as instructions_svc

DISABLED_KEY = "schedules_disabled"   # user.settings_json: built-in job_types switched off

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# job_type -> (human title, the crew role that owns it, one-line what-it-does)
JOB_CATALOG: dict[str, tuple[str, str, str]] = {
    "market_radar": ("Radar sweep", "radar",
                     "Scans your market and reports only material changes."),
    "daily_strategist": ("Daily strategist", "strategist",
                         "Reviews goals and findings, then plans today's operations."),
    "weekly_review": ("Weekly review", "auditor",
                      "Checks last week's predictions against what actually happened."),
    "autopilot": ("Autopilot loop", "orchestrator",
                  "Runs an autonomous loop, such as inbox triage."),
    "instruction_run": ("Automation", "instruction",
                        "One of your saved automations."),
}

BUILT_IN_TYPES = tuple(t for t in JOB_CATALOG if t != "instruction_run")


class ScheduleError(ValueError):
    pass


def role_for(job_type: str) -> str:
    return JOB_CATALOG.get(job_type, (job_type, job_type, ""))[1]


def disabled_types(user: User) -> list[str]:
    raw = (user.settings_json or {}).get(DISABLED_KEY)
    return list(raw) if isinstance(raw, list) else []


def _tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:  # noqa: BLE001 — an unknown timezone must not break the page
        return ZoneInfo("UTC")


def _aware(when: datetime | None) -> datetime | None:
    """Stored timestamps are UTC; SQLite hands them back naive. Never let that
    turn into a local-time reading or an unorderable comparison."""
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def describe_cadence(job: ScheduledJob, tz_name: str) -> str:
    """Human cadence in the user's own timezone: "every day at 06:00 (Europe/Paris)"."""
    seconds = job.interval_seconds
    if not seconds:
        return "one-off"
    next_run = _aware(job.next_run_at)
    local = next_run.astimezone(_tz(tz_name)) if next_run else None
    at = f" at {local:%H:%M}" if local else ""
    where = f" ({tz_name})" if local and tz_name else ""
    if seconds == 3600:
        return "every hour"
    if seconds % 3600 == 0 and seconds < 86400:
        return f"every {seconds // 3600} hours"
    if seconds == 86400:
        return f"every day{at}{where}"
    if seconds == 7 * 86400:
        day = WEEKDAYS[local.weekday()] if local else "week"
        return f"every {day}{at}{where}"
    if seconds % 86400 == 0:
        return f"every {seconds // 86400} days{at}{where}"
    return f"every {max(1, seconds // 60)} minutes"


def _frequency(job: ScheduledJob) -> str | None:
    """The preset this job maps onto, when it maps onto one."""
    return {3600: "hourly", 86400: "daily", 7 * 86400: "weekly"}.get(
        job.interval_seconds or 0)


def _last_result(job: ScheduledJob) -> dict:
    """What happened on the last run, in the shape the page renders."""
    if job.last_error:
        return {"status": "error", "detail": job.last_error[:200]}
    if job.last_run_at is None:
        return {"status": "never_run", "detail": None}
    result = (job.payload_json or {}).get("last_result")
    if isinstance(result, dict) and result.get("skipped"):
        return {"status": "skipped", "detail": str(result["skipped"])[:200]}
    return {"status": "ok", "detail": None}


async def _instruction_for(db: AsyncSession, user_id: str, job: ScheduledJob) -> Instruction | None:
    ins_id = (job.payload_json or {}).get("instruction_id")
    if not ins_id:
        return None
    return (await db.execute(select(Instruction).where(
        Instruction.id == ins_id, Instruction.user_id == user_id))).scalar_one_or_none()


async def serialize(db: AsyncSession, user: User, job: ScheduledJob) -> dict:
    title, role, what = JOB_CATALOG.get(
        job.job_type, (job.job_type.replace("_", " "), job.job_type, ""))
    instruction = await _instruction_for(db, user.id, job)
    if instruction is not None:
        title, what = instruction.name, (instruction.config_json or {}).get(
            "instruction") or what
        role = instruction.assigned_role or role
    return {
        "id": job.id,
        "job_type": job.job_type,
        "title": title,
        "role": role,
        "what": what,
        "cadence": describe_cadence(job, user.timezone),
        "frequency": _frequency(job),
        "interval_seconds": job.interval_seconds,
        "next_run_at": job.next_run_at if job.status == "scheduled" else None,
        "last_run_at": job.last_run_at,
        "last_result": _last_result(job),
        "enabled": job.status == "scheduled",
        "editable": _frequency(job) is not None,
        "instruction_id": instruction.id if instruction is not None else None,
        "timezone": user.timezone,
    }


async def list_for_user(db: AsyncSession, user: User) -> list[dict]:
    """Every recurring job this user has, enabled or not. One-off jobs that have
    already run are execution history, not schedule — they are left out."""
    jobs = list((await db.execute(select(ScheduledJob).where(
        ScheduledJob.user_id == user.id,
        ScheduledJob.status.in_(["scheduled", "running", "cancelled", "failed"]),
    ).order_by(ScheduledJob.next_run_at))).scalars())
    return [await serialize(db, user, j) for j in jobs
            if j.interval_seconds or j.status == "scheduled"]


async def get(db: AsyncSession, user_id: str, job_id: str) -> ScheduledJob:
    job = (await db.execute(select(ScheduledJob).where(
        ScheduledJob.id == job_id, ScheduledJob.user_id == user_id))).scalar_one_or_none()
    if job is None:
        raise ScheduleError("no such scheduled job")
    return job


def _remember_disabled(user: User, job_type: str, disabled: bool) -> None:
    current = [t for t in disabled_types(user) if t != job_type]
    if disabled and job_type in BUILT_IN_TYPES:
        current.append(job_type)
    user.settings_json = {**(user.settings_json or {}), DISABLED_KEY: current}


async def toggle(db: AsyncSession, user: User, job_id: str, enabled: bool) -> dict:
    job = await get(db, user.id, job_id)
    instruction = await _instruction_for(db, user.id, job)
    if instruction is not None:
        # the Instruction owns its schedule: toggling it re-syncs scheduled_jobs
        await instructions_svc.toggle(db, user, instruction.id, enabled)
        refreshed = (await db.execute(select(ScheduledJob).where(
            ScheduledJob.idempotency_key == f"instruction:{instruction.id}",
            ScheduledJob.status == "scheduled"))).scalars().first()
        return await serialize(db, user, refreshed or job)

    _remember_disabled(user, job.job_type, not enabled)
    if enabled:
        job.status = "scheduled"
        job.attempts = 0
        next_run = _aware(job.next_run_at)
        if next_run is None or next_run < datetime.now(UTC):
            # keep the time of day the user had, move it to the next occurrence
            local = (next_run or datetime.now(UTC)).astimezone(_tz(user.timezone))
            job.next_run_at = scheduler.next_local_time(user.timezone, local.hour, local.minute)
    else:
        job.status = "cancelled"
    await db.flush()
    return await serialize(db, user, job)


async def set_cadence(db: AsyncSession, user: User, job_id: str, *, frequency: str,
                      time: str = "08:00", weekday: int = 0) -> dict:
    """Presets only — hourly, daily or weekly at a local time (§86)."""
    if frequency not in instructions_svc.FREQUENCY_SECONDS:
        raise ScheduleError(
            f"frequency must be one of {list(instructions_svc.FREQUENCY_SECONDS)}")
    try:
        hour, minute = (int(x) for x in str(time).split(":"))
        assert 0 <= hour < 24 and 0 <= minute < 60
    except (ValueError, AssertionError) as e:
        raise ScheduleError("time must be HH:MM") from e
    if not (isinstance(weekday, int) and 0 <= weekday <= 6):
        raise ScheduleError("weekday must be 0 (Monday) … 6 (Sunday)")

    job = await get(db, user.id, job_id)
    instruction = await _instruction_for(db, user.id, job)
    if instruction is not None:
        # same path the dashboard uses: the service re-syncs scheduled_jobs
        schedule = {"frequency": frequency, "time": time, "timezone": user.timezone}
        if frequency == "weekly":
            schedule["weekday"] = weekday
        try:
            await instructions_svc.update(db, user, instruction.id, changed_by="user",
                                          reason="schedule page", schedule=schedule)
        except instructions_svc.InstructionError as e:
            raise ScheduleError(str(e)) from e
        refreshed = (await db.execute(select(ScheduledJob).where(
            ScheduledJob.idempotency_key == f"instruction:{instruction.id}",
            ScheduledJob.status == "scheduled"))).scalars().first()
        return await serialize(db, user, refreshed or job)

    job.interval_seconds = instructions_svc.FREQUENCY_SECONDS[frequency]
    job.next_run_at = (
        datetime.now(UTC) if frequency == "hourly"
        else scheduler.next_local_time(user.timezone, hour, minute,
                                       weekday=weekday if frequency == "weekly" else None))
    job.cron_hint = None          # derived from the interval now, never stale
    if job.status == "cancelled":
        job.status = "scheduled"
        _remember_disabled(user, job.job_type, False)
    await db.flush()
    return await serialize(db, user, job)
