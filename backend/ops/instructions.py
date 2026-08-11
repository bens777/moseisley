"""Instruction / automation control layer (third pass §14-§17, §34, §46-§47).

PostgreSQL is canonical. Every instruction has a structured JSON representation
(config/schedule/delivery) that the dashboard exposes in both HUMAN and JSON
views. Every change bumps `version` and stores an immutable snapshot; rollback
re-applies an old snapshot as a NEW version (history is never rewritten).
Scheduling is realized through scheduled_jobs (job_type='instruction_run',
idempotency_key='instruction:{id}').
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Instruction, InstructionVersion, ScheduledJob, User
from backend.jobs import scheduler
from backend.ledger import service as ledger

KINDS = ("market_watch", "goal_review", "budget_rule", "project_instruction",
         "dev_review", "agent_policy", "custom")

FREQUENCY_SECONDS = {"daily": 86400, "weekly": 7 * 86400, "hourly": 3600}


class InstructionError(ValueError):
    pass


def serialize(i: Instruction, *, next_run_at: datetime | None = None) -> dict:
    """The canonical JSON representation shown in the dashboard JSON view."""
    return {
        "id": i.id,
        "name": i.name,
        "kind": i.kind,
        "enabled": i.enabled,
        "owner": "user",
        "created_by": i.created_by,
        "assigned_role": i.assigned_role,
        "provider": i.provider,
        "model": i.model,
        "project_id": i.project_id,
        "config": i.config_json or {},
        "schedule": i.schedule_json or {},
        "delivery": i.delivery_json or [],
        "version": i.version,
        "status": i.status,
        "last_run_at": i.last_run_at,
        "next_run_at": next_run_at,
        "last_result": i.last_result_json or {},
        "created_at": i.created_at,
        "updated_at": i.updated_at,
    }


def _validate(name: str, kind: str, schedule: dict) -> None:
    if not name or not name.strip():
        raise InstructionError("instruction needs a name")
    if kind not in KINDS:
        raise InstructionError(f"kind must be one of {KINDS}")
    if schedule:
        freq = schedule.get("frequency")
        if freq not in FREQUENCY_SECONDS:
            raise InstructionError(f"schedule.frequency must be one of {list(FREQUENCY_SECONDS)}")
        if freq in ("daily", "weekly"):
            time_str = schedule.get("time", "08:00")
            try:
                hour, minute = (int(x) for x in str(time_str).split(":"))
                assert 0 <= hour < 24 and 0 <= minute < 60
            except (ValueError, AssertionError) as e:
                raise InstructionError("schedule.time must be HH:MM") from e
        if freq == "weekly":
            wd = schedule.get("weekday", 0)
            if not (isinstance(wd, int) and 0 <= wd <= 6):
                raise InstructionError("schedule.weekday must be 0 (Monday) … 6 (Sunday)")


async def _sync_schedule(db: AsyncSession, user: User, instruction: Instruction) -> None:
    """Make scheduled_jobs reflect the instruction's schedule deterministically."""
    key = f"instruction:{instruction.id}"
    await db.execute(sa_delete(ScheduledJob).where(
        ScheduledJob.idempotency_key == key,
        ScheduledJob.status.in_(["scheduled", "running"])))
    schedule = instruction.schedule_json or {}
    if not instruction.enabled or not schedule:
        return
    freq = schedule["frequency"]
    tz = schedule.get("timezone") or user.timezone or "UTC"
    if freq == "hourly":
        run_at = datetime.now(UTC)
    else:
        hour, minute = (int(x) for x in str(schedule.get("time", "08:00")).split(":"))
        weekday = schedule.get("weekday") if freq == "weekly" else None
        run_at = scheduler.next_local_time(tz, hour, minute, weekday=weekday)
    await scheduler.enqueue(db, "instruction_run", user_id=user.id,
                            payload={"instruction_id": instruction.id},
                            run_at=run_at, interval_seconds=FREQUENCY_SECONDS[freq],
                            cron_hint=f"{freq} {schedule.get('time', '')} {tz}".strip(),
                            idempotency_key=key)


async def next_run_at(db: AsyncSession, instruction_id: str) -> datetime | None:
    job = (await db.execute(select(ScheduledJob).where(
        ScheduledJob.idempotency_key == f"instruction:{instruction_id}",
        ScheduledJob.status == "scheduled"))).scalars().first()
    return job.next_run_at if job else None


async def _snapshot(db: AsyncSession, instruction: Instruction, *, changed_by: str,
                    reason: str | None) -> None:
    import json as _json

    snapshot = _json.loads(_json.dumps(serialize(instruction), default=str))
    db.add(InstructionVersion(
        instruction_id=instruction.id, user_id=instruction.user_id,
        version=instruction.version, snapshot_json=snapshot,
        changed_by=changed_by, reason=reason))
    await db.flush()


async def get(db: AsyncSession, user_id: str, instruction_id: str) -> Instruction:
    row = (await db.execute(select(Instruction).where(
        Instruction.id == instruction_id, Instruction.user_id == user_id
    ))).scalar_one_or_none()
    if row is None:
        raise InstructionError("instruction not found")
    return row


async def list_for(db: AsyncSession, user_id: str, *, kind: str | None = None,
                   project_id: str | None = None) -> list[Instruction]:
    q = select(Instruction).where(Instruction.user_id == user_id).order_by(Instruction.created_at)
    if kind:
        q = q.where(Instruction.kind == kind)
    if project_id:
        q = q.where(Instruction.project_id == project_id)
    return list((await db.execute(q)).scalars())


async def create(db: AsyncSession, user: User, *, name: str, kind: str,
                 config: dict | None = None, schedule: dict | None = None,
                 delivery: list | None = None, assigned_role: str | None = None,
                 provider: str | None = None, model: str | None = None,
                 project_id: str | None = None, enabled: bool = True,
                 created_by: str = "user", reason: str | None = None) -> Instruction:
    _validate(name, kind, schedule or {})
    instruction = Instruction(
        user_id=user.id, project_id=project_id, name=name.strip(), kind=kind,
        enabled=enabled, assigned_role=assigned_role, provider=provider, model=model,
        config_json=config or {}, schedule_json=schedule or {},
        delivery_json=delivery or [], created_by=created_by, version=1)
    db.add(instruction)
    await db.flush()
    await _snapshot(db, instruction, changed_by=created_by, reason=reason or "created")
    await _sync_schedule(db, user, instruction)
    await ledger.record(db, user.id, "instruction_created", actor_type=created_by,
                        entity_type="instruction", entity_id=instruction.id,
                        payload={"name": instruction.name, "kind": kind})
    return instruction


async def update(db: AsyncSession, user: User, instruction_id: str, *,
                 changed_by: str = "user", reason: str | None = None,
                 **fields) -> Instruction:
    instruction = await get(db, user.id, instruction_id)
    name = fields.get("name", instruction.name)
    kind = fields.get("kind", instruction.kind)
    schedule = fields.get("schedule", instruction.schedule_json)
    _validate(name, kind, schedule or {})
    instruction.name = name.strip()
    instruction.kind = kind
    instruction.schedule_json = schedule or {}
    for src, attr in (("config", "config_json"), ("delivery", "delivery_json")):
        if src in fields and fields[src] is not None:
            setattr(instruction, attr, fields[src])
    for attr in ("assigned_role", "provider", "model", "project_id", "enabled"):
        if attr in fields and fields[attr] is not None:
            setattr(instruction, attr, fields[attr])
    instruction.version += 1
    await db.flush()
    await _snapshot(db, instruction, changed_by=changed_by, reason=reason or "updated")
    await _sync_schedule(db, user, instruction)
    await ledger.record(db, user.id, "instruction_updated", actor_type=changed_by,
                        entity_type="instruction", entity_id=instruction.id,
                        payload={"version": instruction.version, "reason": reason})
    return instruction


async def toggle(db: AsyncSession, user: User, instruction_id: str, enabled: bool,
                 *, changed_by: str = "user") -> Instruction:
    instruction = await get(db, user.id, instruction_id)
    instruction.enabled = enabled
    instruction.version += 1
    await db.flush()
    await _snapshot(db, instruction, changed_by=changed_by,
                    reason="enabled" if enabled else "disabled")
    await _sync_schedule(db, user, instruction)
    await ledger.record(db, user.id, "instruction_toggled", actor_type=changed_by,
                        entity_type="instruction", entity_id=instruction.id,
                        payload={"enabled": enabled})
    return instruction


async def duplicate(db: AsyncSession, user: User, instruction_id: str) -> Instruction:
    src = await get(db, user.id, instruction_id)
    return await create(
        db, user, name=f"{src.name} (copy)", kind=src.kind,
        config=dict(src.config_json or {}), schedule=dict(src.schedule_json or {}),
        delivery=list(src.delivery_json or []), assigned_role=src.assigned_role,
        provider=src.provider, model=src.model, project_id=src.project_id,
        enabled=False, created_by="user", reason=f"duplicated from {src.id}")


async def rollback(db: AsyncSession, user: User, instruction_id: str, version: int,
                   *, changed_by: str = "user") -> Instruction:
    """Re-apply an old snapshot as a NEW version (history preserved)."""
    await get(db, user.id, instruction_id)  # tenancy check
    snap = (await db.execute(select(InstructionVersion).where(
        InstructionVersion.instruction_id == instruction_id,
        InstructionVersion.version == version))).scalar_one_or_none()
    if snap is None:
        raise InstructionError(f"version {version} not found")
    s = snap.snapshot_json
    return await update(db, user, instruction_id, changed_by=changed_by,
                        reason=f"rollback to v{version}",
                        name=s["name"], kind=s["kind"], config=s["config"],
                        schedule=s["schedule"], delivery=s["delivery"],
                        assigned_role=s["assigned_role"], provider=s["provider"],
                        model=s["model"], enabled=s["enabled"])


async def remove(db: AsyncSession, user: User, instruction_id: str) -> None:
    instruction = await get(db, user.id, instruction_id)
    instruction.enabled = False
    await _sync_schedule(db, user, instruction)  # cancels the scheduled job
    await db.execute(sa_delete(InstructionVersion).where(
        InstructionVersion.instruction_id == instruction_id))
    await db.delete(instruction)
    await ledger.record(db, user.id, "instruction_deleted", actor_type="user",
                        entity_type="instruction", entity_id=instruction_id,
                        payload={"name": instruction.name})


async def versions(db: AsyncSession, user_id: str, instruction_id: str) -> list[InstructionVersion]:
    await get(db, user_id, instruction_id)  # tenancy check
    return list((await db.execute(select(InstructionVersion).where(
        InstructionVersion.instruction_id == instruction_id)
        .order_by(InstructionVersion.version.desc()))).scalars())


async def record_run_result(db: AsyncSession, instruction: Instruction,
                            result: dict, *, status: str = "active") -> None:
    instruction.last_run_at = datetime.now(UTC)
    instruction.last_result_json = result
    instruction.status = status
    await db.flush()
    await ledger.record(db, instruction.user_id, "instruction_run_completed",
                        actor_type="system", entity_type="instruction",
                        entity_id=instruction.id,
                        payload={"status": status, "summary": str(result)[:300]})
