"""Skill lifecycle: enable, disable, report state.

Every write goes through a path that already exists — crew config, the
instructions service, scheduler.enqueue. This module owns exactly one new
thing: the record of what a skill changed, so disabling it can put those things
back and nothing else.

That record is the whole reason disable is safe. A role is not "switched off"
on disable — it is RESTORED to whatever it was before the skill touched it. A
user who already had Radar on and then tries a skill does not lose Radar when
they change their mind.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import crew
from backend.billing import entitlements
from backend.core.models import CrewConfig, Instruction, LlmUsage, ScheduledJob, SystemSetting, User
from backend.jobs import scheduler
from backend.ledger import service as ledger
from backend.ops import instructions as instructions_svc
from backend.skills import catalog

logger = logging.getLogger("mychief.skills")

STATE_KEY = "skills"          # SystemSetting: {skill_id: {...what we changed...}}


class SkillError(ValueError):
    pass


class SkillGated(Exception):
    """The user's plan does not include something this skill needs."""

    def __init__(self, feature: str, detail: str):
        self.feature = feature
        self.detail = detail
        super().__init__(detail)


# ── per-user state ──────────────────────────────────────────────────

async def _row(db: AsyncSession, user_id: str) -> SystemSetting:
    row = (await db.execute(select(SystemSetting).where(
        SystemSetting.user_id == user_id,
        SystemSetting.key == STATE_KEY))).scalar_one_or_none()
    if row is None:
        row = SystemSetting(user_id=user_id, key=STATE_KEY, value_json={})
        db.add(row)
        await db.flush()
    return row


async def state_for(db: AsyncSession, user_id: str) -> dict:
    return dict((await _row(db, user_id)).value_json or {})


async def _save(db: AsyncSession, user_id: str, state: dict) -> None:
    row = await _row(db, user_id)
    row.value_json = state
    await db.flush()


def enabled_ids(state: dict) -> list[str]:
    return sorted(k for k, v in state.items() if (v or {}).get("enabled"))


# ── gating ──────────────────────────────────────────────────────────

async def gate_reason(db: AsyncSession, user_id: str, skill: catalog.Skill) -> tuple[str, str] | None:
    """(feature, reason) when the plan blocks this skill, else None."""
    for feature in skill.features:
        if not await entitlements.check_feature(db, user_id, feature):
            return feature, (f"'{feature}' requires the Pro plan ($19/month). "
                             "Upgrade in Settings → Billing.")
    return None


# ── enable ──────────────────────────────────────────────────────────

def _time_parts(value: str, fallback: str) -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in str(value).split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
        return hour, minute
    except (ValueError, AttributeError):
        hour, minute = (int(x) for x in fallback.split(":"))
        return hour, minute


def _job_key(skill: catalog.Skill, entry: catalog.ScheduleEntry, user_id: str, index: int) -> str:
    if entry.shared_key:
        # the platform's own key for this built-in: enqueue dedupes against the
        # sweep the user already has instead of adding a second one
        return f"{entry.job_type}:{user_id}"
    return f"skill:{skill.id}:{index}:{user_id}"


async def enable(db: AsyncSession, user: User, skill_id: str,
                 config: dict | None = None) -> dict:
    skill = catalog.BY_ID.get(skill_id)
    if skill is None:
        raise SkillError(f"unknown skill: {skill_id}")

    gate = await gate_reason(db, user.id, skill)
    if gate is not None:
        raise SkillGated(*gate)

    state = await state_for(db, user.id)
    if (state.get(skill_id) or {}).get("enabled"):
        return state[skill_id]                      # idempotent: never composes twice

    config = {**{c.key: c.default for c in skill.config_fields}, **(config or {})}
    record: dict = {"enabled": True, "enabled_at": datetime.now(UTC).isoformat(),
                    "config": config, "roles_prior": {}, "job_ids": [],
                    "instruction_ids": []}

    # 1. roles — remember what they were, then turn them on
    for role in skill.roles:
        cfg = await crew.get_config(db, user.id, role)
        if cfg is None:
            # no row means "on by default"; record that so disable restores it
            record["roles_prior"][role] = True
            cfg = CrewConfig(user_id=user.id, role=role)
            db.add(cfg)
        else:
            record["roles_prior"][role] = bool(cfg.enabled)
        cfg.enabled = True
    await db.flush()

    # 2. instructions — the automation record the scheduled job points at
    for entry in skill.instructions:
        instruction = await instructions_svc.create(
            db, user, name=entry.name, kind=entry.kind,
            config={"instruction": entry.task}, schedule={},   # the skill owns the cadence
            delivery=[], assigned_role=entry.assigned_role,
            created_by="skill", reason=f"skill:{skill.id}")
        record["instruction_ids"].append(instruction.id)

    # 3. schedules — existing job types only, through the existing scheduler
    for index, entry in enumerate(skill.schedules):
        hour, minute = _time_parts(config.get("run_time", entry.default_time),
                                   entry.default_time)
        if index and not entry.shared_key:
            minute = (minute + 15 * index) % 60      # stagger, never all at once
        payload = dict(entry.payload)
        if entry.job_type == "instruction_run" and record["instruction_ids"]:
            payload["instruction_id"] = record["instruction_ids"][0]
        job = await scheduler.enqueue(
            db, entry.job_type, user_id=user.id, payload=payload,
            run_at=scheduler.next_local_time(user.timezone, hour, minute,
                                             weekday=entry.weekday,
                                             day_of_month=entry.day_of_month),
            interval_seconds=entry.interval_seconds,
            idempotency_key=_job_key(skill, entry, user.id, index))
        if job is not None:
            record["job_ids"].append(job.id)
        # job is None → an equivalent job already exists (a built-in sweep the
        # user already has). We adopt it and do NOT record it, so disabling the
        # skill will not cancel something the platform owns.

    state[skill_id] = record
    await _save(db, user.id, state)
    await ledger.record(db, user.id, "skill_enabled", actor_type="user",
                        entity_type="skill", entity_id=skill_id,
                        payload={"name": skill.name, "roles": list(skill.roles),
                                 "jobs": len(record["job_ids"])})
    return record


# ── disable ─────────────────────────────────────────────────────────

async def disable(db: AsyncSession, user: User, skill_id: str) -> dict:
    skill = catalog.BY_ID.get(skill_id)
    if skill is None:
        raise SkillError(f"unknown skill: {skill_id}")
    state = await state_for(db, user.id)
    record = state.get(skill_id) or {}
    if not record.get("enabled"):
        return {"enabled": False}

    # 1. roles back to exactly what they were
    for role, prior in (record.get("roles_prior") or {}).items():
        cfg = await crew.get_config(db, user.id, role)
        if cfg is not None:
            cfg.enabled = bool(prior)

    # 2. automations switched off, never deleted — the record and its history stay
    for instruction_id in record.get("instruction_ids") or []:
        instruction = (await db.execute(select(Instruction).where(
            Instruction.id == instruction_id,
            Instruction.user_id == user.id))).scalar_one_or_none()
        if instruction is not None:
            instruction.enabled = False

    # 3. only the jobs this skill created
    for job_id in record.get("job_ids") or []:
        job = (await db.execute(select(ScheduledJob).where(
            ScheduledJob.id == job_id,
            ScheduledJob.user_id == user.id))).scalar_one_or_none()
        if job is not None and job.status in ("scheduled", "running"):
            job.status = "cancelled"

    state[skill_id] = {**record, "enabled": False,
                       "disabled_at": datetime.now(UTC).isoformat()}
    await _save(db, user.id, state)
    await ledger.record(db, user.id, "skill_disabled", actor_type="user",
                        entity_type="skill", entity_id=skill_id,
                        payload={"name": skill.name})
    return state[skill_id]


# ── reporting ───────────────────────────────────────────────────────

async def _last_activity(db: AsyncSession, user_id: str, skill: catalog.Skill,
                         record: dict) -> str | None:
    """Most recent evidence this skill did something: a job run, else crew usage."""
    stamps: list[datetime] = []
    for job_id in record.get("job_ids") or []:
        job = (await db.execute(select(ScheduledJob).where(
            ScheduledJob.id == job_id))).scalar_one_or_none()
        if job is not None and job.last_run_at:
            stamps.append(job.last_run_at)
    for role in skill.roles:
        usage = (await db.execute(select(LlmUsage).where(
            LlmUsage.user_id == user_id, LlmUsage.crew_role == role
        ).order_by(LlmUsage.created_at.desc()).limit(1))).scalars().first()
        if usage is not None:
            stamps.append(usage.created_at)
    if not stamps:
        return None
    return max(s.isoformat() if hasattr(s, "isoformat") else str(s) for s in stamps)


async def list_for_user(db: AsyncSession, user: User) -> list[dict]:
    state = await state_for(db, user.id)
    out = []
    for skill in catalog.CATALOG:
        record = state.get(skill.id) or {}
        gate = await gate_reason(db, user.id, skill)
        enabled = bool(record.get("enabled"))
        out.append({
            **catalog.serialize(skill),
            "enabled": enabled,
            "gated": gate is not None and not enabled,
            "gate_reason": gate[1] if gate else None,
            "gate_feature": gate[0] if gate else None,
            "enabled_at": record.get("enabled_at"),
            "config": record.get("config") or {},
            "last_activity": await _last_activity(db, user.id, skill, record)
                             if enabled else None,
        })
    return out
