"""Scheduled job handlers. Reasoning is event/schedule-triggered — never an infinite
LLM loop (§86)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.billing import entitlements
from backend.core import killswitch
from backend.core.models import ScheduledJob, User
from backend.jobs.scheduler import handler
from backend.strategy import autopilot
from backend.strategy.strategist import run_daily_strategist


async def _user(db: AsyncSession, job: ScheduledJob) -> User | None:
    if not job.user_id:
        return None
    return (await db.execute(select(User).where(User.id == job.user_id))).scalar_one_or_none()


async def _autonomy_allowed(db: AsyncSession, user: User) -> bool:
    """Scheduled autonomous work is a Pro feature on hosted deployments."""
    return await entitlements.check_feature(db, user.id, "scheduled_autonomy")


@handler("daily_strategist")
async def daily_strategist_job(db: AsyncSession, job: ScheduledJob) -> dict:
    user = await _user(db, job)
    if user is None:
        return {"skipped": "no user"}
    if not await _autonomy_allowed(db, user):
        return {"skipped": "scheduled autonomy requires the Pro plan"}
    try:
        plan = await run_daily_strategist(db, user)
        return {"no_action": plan["no_action"], "priorities": len(plan["top_priorities"])}
    except killswitch.KillSwitchEngaged as e:
        return {"skipped": str(e)}


@handler("autopilot")
async def autopilot_job(db: AsyncSession, job: ScheduledJob) -> dict:
    user = await _user(db, job)
    if user is None:
        return {"skipped": "no user"}
    if not await _autonomy_allowed(db, user):
        return {"skipped": "scheduled autonomy requires the Pro plan"}
    loop = job.payload_json.get("loop", "inbox_triage")
    try:
        result = await autopilot.run_loop(db, user, loop)
        return {"loop": loop, "status": result.status, "summary": result.summary}
    except killswitch.KillSwitchEngaged as e:
        return {"skipped": str(e)}


@handler("market_radar")
async def market_radar_job(db: AsyncSession, job: ScheduledJob) -> dict:
    user = await _user(db, job)
    if user is None:
        return {"skipped": "no user"}
    if not await _autonomy_allowed(db, user):
        return {"skipped": "scheduled autonomy requires the Pro plan"}
    from backend.market.radar import run_market_scan

    try:
        result = await run_market_scan(db, user)
        return result
    except killswitch.KillSwitchEngaged as e:
        return {"skipped": str(e)}


@handler("instruction_run")
async def instruction_run_job(db: AsyncSession, job: ScheduledJob) -> dict:
    """Generic executor for user-authored instructions (third pass §15-§16)."""
    user = await _user(db, job)
    if user is None:
        return {"skipped": "no user"}
    if not await _autonomy_allowed(db, user):
        return {"skipped": "scheduled autonomy requires the Pro plan"}
    from sqlalchemy import select

    from backend.core.models import Instruction

    instruction = (await db.execute(select(Instruction).where(
        Instruction.id == (job.payload_json or {}).get("instruction_id"),
        Instruction.user_id == user.id))).scalar_one_or_none()
    if instruction is None or not instruction.enabled:
        return {"skipped": "instruction missing or disabled"}
    try:
        if instruction.kind == "market_watch":
            from backend.market.watches import run_watch

            return await run_watch(db, user, instruction)
        if instruction.kind == "dev_review":
            from backend.agents.dev import run_dev_review

            return await run_dev_review(db, user, instruction)
        # generic kinds: bounded delegation to the assigned crew role
        from backend.agents import crew
        from backend.ops import instructions as instructions_svc

        role = instruction.assigned_role or "strategist"
        task = (instruction.config_json or {}).get("instruction") or instruction.name
        run = await crew.delegate(db, user, role, task, orchestrator_run_id=None)
        result = {"crew_run_id": run.id, "status": run.status}
        await instructions_svc.record_run_result(db, instruction, result)
        return result
    except killswitch.KillSwitchEngaged as e:
        return {"skipped": str(e)}


@handler("weekly_review")
async def weekly_review_job(db: AsyncSession, job: ScheduledJob) -> dict:
    user = await _user(db, job)
    if user is None:
        return {"skipped": "no user"}
    if not await _autonomy_allowed(db, user):
        return {"skipped": "scheduled autonomy requires the Pro plan"}
    from backend.strategy.auditor import run_weekly_review

    try:
        report = await run_weekly_review(db, user)
        return {"status": "completed", "predictions_reviewed": report.get("predictions_reviewed", 0)}
    except killswitch.KillSwitchEngaged as e:
        return {"skipped": str(e)}
