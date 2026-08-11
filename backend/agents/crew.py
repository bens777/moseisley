"""Crew roles (owner directive §14-18): role ≠ runtime.

Roles are the specialists the Orchestrator coordinates. Each role has a source-controlled
default prompt (backend/prompts/) with an optional DB override, and a model policy
(inherit orchestrator | custom). Delegations are bounded and tracked in crew_runs.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import CrewConfig, CrewRun, LlmUsage, User
from backend.ledger import service as ledger

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# role -> (display name, mission line)
ROLES: dict[str, tuple[str, str]] = {
    "orchestrator": ("Orchestrator", "Primary intelligence and delegation"),
    "strategist": ("Strategist", "Strategy and prioritization"),
    "challenger": ("Challenger", "Attempts to prove the current strategy wrong"),
    "xray": ("X-Ray", "Analyzes historical and current reality"),
    "radar": ("Radar", "External market intelligence"),
    "auditor": ("Auditor", "Prediction/outcome verification and learning"),
    "goal_compiler": ("Goal Compiler", "Natural-language goals → structured goals"),
    "follow_up": ("Follow-Up", "Commercial follow-up / lost lead recovery"),
    "commitment_tracker": ("Commitment Tracker", "Tracks unresolved important promises"),
    "inbox_triage": ("Inbox Triage", "Email classification and prioritization"),
    # third pass (2026-08-11)
    "manager": ("Manager", "Your conversational interface to the whole operation"),
    "dev": ("Dev Agent", "Continuously improves the Moseisley platform itself"),
}

# Deterministic orchestration limits (§18)
MAX_DELEGATIONS_PER_RUN = 3
MAX_LLM_CALLS_PER_RUN = 8


def default_prompt(role: str) -> str:
    base = (PROMPTS_DIR / "shared" / "crew_base.md").read_text()
    path = PROMPTS_DIR / f"{role}.md"
    role_text = path.read_text() if path.exists() else ""
    return f"{role_text}\n\n---\n{base}" if role != "orchestrator" else role_text


async def get_config(db: AsyncSession, user_id: str, role: str) -> CrewConfig | None:
    return (await db.execute(select(CrewConfig).where(
        CrewConfig.user_id == user_id, CrewConfig.role == role
    ))).scalar_one_or_none()


async def get_prompt(db: AsyncSession, user_id: str, role: str) -> str:
    cfg = await get_config(db, user_id, role)
    if cfg is not None and not cfg.uses_default_prompt and cfg.custom_prompt:
        return cfg.custom_prompt
    return default_prompt(role)


async def set_prompt(db: AsyncSession, user_id: str, role: str,
                     custom_prompt: str | None) -> CrewConfig:
    """custom_prompt=None resets to the source-controlled default."""
    if role not in ROLES:
        raise ValueError(f"unknown crew role: {role}")
    cfg = await get_config(db, user_id, role)
    if cfg is None:
        cfg = CrewConfig(user_id=user_id, role=role)
        db.add(cfg)
    if custom_prompt is None:
        cfg.custom_prompt = None
        cfg.uses_default_prompt = True
    else:
        cfg.custom_prompt = custom_prompt
        cfg.uses_default_prompt = False
    cfg.prompt_version = (cfg.prompt_version or 0) + 1
    await db.flush()
    await ledger.record(db, user_id, "prompt_changed", actor_type="user",
                        entity_type="crew_config", entity_id=cfg.id,
                        payload={"role": role, "uses_default": cfg.uses_default_prompt,
                                 "version": cfg.prompt_version})
    return cfg


async def set_model_policy(db: AsyncSession, user_id: str, role: str, *,
                           model_policy: str, provider: str | None = None,
                           model: str | None = None) -> CrewConfig:
    if role not in ROLES:
        raise ValueError(f"unknown crew role: {role}")
    if model_policy not in ("inherit", "custom"):
        raise ValueError("model_policy must be inherit or custom")
    if model_policy == "custom" and not provider:
        raise ValueError("custom model policy requires a provider")
    cfg = await get_config(db, user_id, role)
    if cfg is None:
        cfg = CrewConfig(user_id=user_id, role=role)
        db.add(cfg)
    cfg.model_policy = model_policy
    cfg.provider = provider if model_policy == "custom" else None
    cfg.model = model if model_policy == "custom" else None
    await db.flush()
    await ledger.record(db, user_id, "crew_model_changed", actor_type="user",
                        entity_type="crew_config", entity_id=cfg.id,
                        payload={"role": role, "model_policy": model_policy,
                                 "provider": provider, "model": model})
    return cfg


async def role_usage_this_month(db: AsyncSession, user_id: str) -> dict[str, dict]:
    from datetime import timedelta

    since = datetime.now(UTC) - timedelta(days=30)
    rows = list((await db.execute(select(LlmUsage).where(
        LlmUsage.user_id == user_id, LlmUsage.created_at >= since
    ))).scalars())
    out: dict[str, dict] = {}
    for r in rows:
        role = r.crew_role or ("orchestrator" if r.purpose in ("chat",) else r.purpose)
        agg = out.setdefault(role, {"requests": 0, "total_tokens": 0,
                                    "reported_cost": 0.0, "estimated_cost": 0.0,
                                    "unknown_cost_requests": 0})
        agg["requests"] += 1
        agg["total_tokens"] += r.total_tokens or ((r.input_tokens or 0) + (r.output_tokens or 0))
        if r.cost_source == "PROVIDER_REPORTED" and r.provider_reported_cost is not None:
            agg["reported_cost"] += r.provider_reported_cost
        elif r.cost_source == "ESTIMATED" and r.estimated_cost is not None:
            agg["estimated_cost"] += r.estimated_cost
        else:
            agg["unknown_cost_requests"] += 1
    return out


async def last_runs(db: AsyncSession, user_id: str, limit: int = 20) -> list[CrewRun]:
    return list((await db.execute(
        select(CrewRun).where(CrewRun.user_id == user_id)
        .order_by(CrewRun.started_at.desc()).limit(limit)
    )).scalars())


# --- Bounded delegation (§18): orchestrator → crew role → structured result ---

DELEGATABLE = ("strategist", "challenger", "xray", "radar", "auditor")


async def delegate(db: AsyncSession, user: User, role: str, task: str, *,
                   orchestrator_run_id: str) -> dict:
    """Run one bounded crew job and return a structured, summarizable result."""
    if role not in DELEGATABLE:
        return {"error": f"role '{role}' cannot be delegated to"}
    existing = (await db.execute(select(CrewRun).where(
        CrewRun.orchestrator_run_id == orchestrator_run_id
    ))).scalars().all()
    if len(existing) >= MAX_DELEGATIONS_PER_RUN:
        return {"error": "delegation limit reached for this run"}

    run = CrewRun(user_id=user.id, orchestrator_run_id=orchestrator_run_id,
                  crew_role=role, runtime="native", task_summary=task[:500])
    db.add(run)
    await db.flush()
    await ledger.record(db, user.id, "crew_run_started", actor_type="agent",
                        actor_id="orchestrator", entity_type="crew_run", entity_id=run.id,
                        payload={"role": role, "task": task[:200]})
    try:
        if role == "strategist":
            from backend.strategy.strategist import run_daily_strategist

            plan = await run_daily_strategist(db, user)
            result = {"summary": plan.get("summary"), "no_action": plan.get("no_action"),
                      "top_priorities": plan.get("top_priorities")}
        elif role == "challenger":
            from backend.market.challenger import run_challenger

            outcome = await run_challenger(db, user)
            result = {"verdict": outcome.get("verdict"),
                      "arguments": outcome.get("arguments"),
                      "proposed_micro_tests": outcome.get("proposed_micro_tests")}
        elif role == "radar":
            from backend.market.radar import run_market_scan

            outcome = await run_market_scan(db, user)
            result = outcome
        elif role == "xray":
            from backend.xray.engine import run_xray

            xr = await run_xray(db, user.id, 90)
            result = {"status": xr.status, "summary": xr.summary_json}
        else:  # auditor
            from backend.strategy.auditor import run_weekly_review

            report = await run_weekly_review(db, user)
            result = {"predictions_reviewed": report.get("predictions_reviewed"),
                      "calibration": report.get("calibration")}
        run.status = "completed"
        run.result_summary = json.dumps(result, default=str)[:4000]
    except Exception as e:  # noqa: BLE001 - delegation failure is a result, not a crash
        run.status = "failed"
        run.result_summary = f"{type(e).__name__}: {e}"[:500]
        result = {"error": run.result_summary}
    run.finished_at = datetime.now(UTC)
    await ledger.record(db, user.id, "crew_run_completed", actor_type="agent",
                        actor_id="orchestrator", entity_type="crew_run", entity_id=run.id,
                        payload={"role": role, "status": run.status})
    # attribute the delegation's provider/model from its usage records
    usage = (await db.execute(select(LlmUsage).where(
        LlmUsage.user_id == user.id, LlmUsage.crew_role == role
    ).order_by(LlmUsage.created_at.desc()).limit(1))).scalars().first()
    if usage is not None:
        run.provider = usage.provider
        run.requested_model = usage.requested_model
        run.actual_model = usage.model
    await db.flush()
    return result
