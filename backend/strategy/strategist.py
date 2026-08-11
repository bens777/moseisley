"""Daily Strategist (§58): what deserves attention now?

LLM reasoning over the real World Model, with deterministic validation (max 3 primary
actions) and a deterministic fallback when no LLM provider is available. NO_ACTION is
a valid, first-class outcome (§142).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch
from backend.core.models import User, XRayFinding
from backend.documents import service as documents
from backend.ledger import service as ledger
from backend.life_kernel import world_model
from backend.providers import registry
from backend.providers.clients import ProviderError
from backend.providers.registry import LlmBudgetExceeded, NoProviderAvailable

SYSTEM_PROMPT = """You are the Daily Strategist agent of the user's AI crew on Moseisley.sh.
Given the user's goals, current state and open findings, decide what deserves attention today.
Reply ONLY with JSON:
{
  "summary": "...",
  "no_action": false,
  "top_priorities": [{"title": "...", "why": "...", "linked_goal": null}],
  "background_actions": ["..."],
  "proposed_experiments": ["..."],
  "risks": ["..."],
  "confidence": 0.0
}
Rules: at most 3 top_priorities. Use the user's ACTUAL goals and findings — never generic
productivity advice. If nothing materially needs the user today, set no_action=true and
leave top_priorities empty. Do not invent data."""


def _deterministic_plan(snapshot: dict, findings: list[XRayFinding]) -> dict:
    """Fallback when no LLM is available: priorities from verified findings and goals."""
    priorities: list[dict] = []
    for f in sorted(findings, key=lambda x: (not x.verified, -(x.estimated_value_cents or 0))):
        if len(priorities) >= 3:
            break
        if f.recommended_action and f.status == "open":
            priorities.append({
                "title": f.recommended_action[:140],
                "why": f.title,
                "linked_goal": None,
            })
    if not priorities and snapshot["pending_approvals"]:
        priorities.append({"title": f"Resolve {snapshot['pending_approvals']} pending approval(s)",
                           "why": "Decisions are waiting on you.", "linked_goal": None})
    return {
        "summary": ("Nothing material needs you today." if not priorities
                    else "Highest-value open items selected from verified findings."),
        "no_action": not priorities,
        "top_priorities": priorities,
        "background_actions": [],
        "proposed_experiments": [],
        "risks": [],
        "confidence": 0.5,
        "source": "deterministic_fallback",
    }


async def run_daily_strategist(db: AsyncSession, user: User) -> dict:
    await killswitch.require_off(db, user.id, killswitch.PAUSE_ALL_AGENTS)
    snapshot = await world_model.snapshot(db, user.id)
    findings = list((await db.execute(
        select(XRayFinding).where(XRayFinding.user_id == user.id, XRayFinding.status == "open")
        .order_by(XRayFinding.created_at.desc()).limit(30)
    )).scalars())

    plan: dict | None = None
    try:
        context = {
            "world": snapshot,
            "open_findings": [
                {"type": f.type, "title": f.title, "verified": f.verified,
                 "recommended_action": f.recommended_action,
                 "estimated_value_cents": f.estimated_value_cents}
                for f in findings[:15]
            ],
        }
        result = await registry.complete(db, user.id, "strategy", [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, default=str)},
        ], json_mode=True, max_tokens=1200)
        parsed = result.parse_json()
        if isinstance(parsed, dict) and "top_priorities" in parsed:
            plan = parsed
            plan["source"] = "llm"
    except (NoProviderAvailable, LlmBudgetExceeded, ProviderError, killswitch.KillSwitchEngaged):
        plan = None

    if plan is None:
        plan = _deterministic_plan(snapshot, findings)

    # Deterministic guarantees regardless of what the LLM said (§58).
    plan["top_priorities"] = list(plan.get("top_priorities") or [])[:3]
    plan["no_action"] = bool(plan.get("no_action")) or not plan["top_priorities"]
    plan["generated_at"] = datetime.now(UTC).isoformat()

    lines = [f"# Daily Strategist — {plan['generated_at'][:10]}", "", plan.get("summary", ""), ""]
    if plan["no_action"]:
        lines.append("**NO_ACTION** — nothing materially needs you today.")
    else:
        lines.append("## Top priorities")
        for i, p in enumerate(plan["top_priorities"], 1):
            title = p.get("title") if isinstance(p, dict) else str(p)
            why = p.get("why", "") if isinstance(p, dict) else ""
            lines.append(f"{i}. **{title}**" + (f" — {why}" if why else ""))
    await documents.upsert_document(db, user.id, "/reports/daily-strategist.md",
                                    "\n".join(lines) + "\n", actor_type="system")
    await ledger.record(db, user.id, "strategy_proposed", actor_type="agent", actor_id="strategist",
                        payload={"no_action": plan["no_action"],
                                 "priorities": len(plan["top_priorities"]),
                                 "source": plan.get("source")})
    await killswitch.set_setting(db, user.id, "latest_strategist_plan", plan)
    await db.flush()
    return plan
