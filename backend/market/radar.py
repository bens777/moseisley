"""Market Radar (§64-68): external evidence that could change the user's best allocation.

Sensor: the 'market' purpose in the ProviderRegistry (xAI/Grok preferred). Signals are
UNTRUSTED content (§80): they are stored and scored but can never mutate policy,
permissions, budgets or secrets. Most days the correct outcome is NO MATERIAL CHANGE (§67).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch
from backend.core.models import Goal, MarketSignal, Opportunity, Project, User
from backend.jobs.scheduler import handler as job_handler  # noqa: F401 (registration side effects live here)
from backend.ledger import service as ledger
from backend.market import hysteresis
from backend.providers import registry
from backend.providers.clients import ProviderError
from backend.providers.registry import LlmBudgetExceeded, NoProviderAvailable

logger = logging.getLogger("mychief.market")

SCAN_PROMPT = """You are the Market Radar agent of the user's AI crew on Moseisley.sh. The user's context:
{context}

Search your knowledge for CURRENT external evidence that could materially change how this
user should allocate time or capital: accelerating pain, buyers requesting solutions,
new technologies/APIs/regulations, pricing disruption, competitor moves.

Reply ONLY with JSON:
{{"signals": [{{"title": "...", "content": "...", "url": null,
   "evidence_level": "attention|interest|pain|commercial_intent|purchase|revenue",
   "strength": 0.0, "buyer": "...", "problem": "..."}}]}}

Rules: report only evidence you can describe concretely. An empty signals list is a good
answer when nothing material exists. Never exaggerate evidence_level: a popular topic is
"attention", not "commercial_intent"."""

_ALLOWED_LEVELS = set(hysteresis.EVIDENCE_ORDER)


async def _user_context(db: AsyncSession, user_id: str) -> str:
    goals = list((await db.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active")
    )).scalars())
    projects = list((await db.execute(
        select(Project).where(Project.user_id == user_id, Project.status.in_(["active", "experiment"]))
    )).scalars())
    parts = []
    for g in goals:
        parts.append(f"Goal: {g.title} ({g.metric} → {g.target_value} {g.unit or ''})")
    for p in projects:
        parts.append(f"Project: {p.name} — strategy: {p.strategy or 'n/a'}")
    return "\n".join(parts) or "No goals defined yet."


def _sanitize_signal(raw: dict) -> dict | None:
    """Deterministic validation of untrusted LLM/market output."""
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("content") or "").strip()[:2000]
    if not content:
        return None
    level = str(raw.get("evidence_level") or "attention").lower()
    if level not in _ALLOWED_LEVELS:
        level = "attention"
    try:
        strength = max(0.0, min(1.0, float(raw.get("strength", 0))))
    except (TypeError, ValueError):
        strength = 0.0
    return {
        "title": str(raw.get("title") or content[:80]),
        "content": content,
        "url": (str(raw.get("url"))[:1024] if raw.get("url") else None),
        "evidence_level": level,
        "strength": strength,
        "buyer": str(raw.get("buyer") or "")[:300] or None,
        "problem": str(raw.get("problem") or "")[:1000] or None,
    }


async def run_market_scan(db: AsyncSession, user: User) -> dict:
    await killswitch.require_off(db, user.id, killswitch.PAUSE_ALL_AGENTS)
    await ledger.record(db, user.id, "market_scan_started", actor_type="agent", actor_id="market_radar")

    signals: list[dict] = []
    scan_source = "none"
    try:
        context = await _user_context(db, user.id)
        result = await registry.complete(db, user.id, "market", [
            {"role": "system", "content": "You detect market signals. JSON only."},
            {"role": "user", "content": SCAN_PROMPT.format(context=context)},
        ], json_mode=True, max_tokens=1500)
        parsed = result.parse_json()
        if isinstance(parsed, dict):
            signals = [s for s in map(_sanitize_signal, parsed.get("signals") or []) if s]
            scan_source = "llm"
    except (NoProviderAvailable, LlmBudgetExceeded, ProviderError):
        scan_source = "no_provider"

    for s in signals:
        db.add(MarketSignal(
            user_id=user.id, source=f"market_scan:{scan_source}", content=s["content"],
            url=s["url"], evidence_level=s["evidence_level"], strength=s["strength"],
            metadata_json={"title": s["title"], "buyer": s["buyer"], "problem": s["problem"]},
        ))
    await db.flush()

    # Deterministic materiality gate (§60, §139) — the LLM cannot decide this.
    verdict = hysteresis.pivot_verdict(signals)
    opportunity_id = None
    if verdict == "PROPOSE_MICRO_TEST":
        top = sorted(signals, key=lambda s: -s["strength"])
        title = top[0]["title"][:300]
        existing = (await db.execute(
            select(Opportunity).where(Opportunity.user_id == user.id, Opportunity.title == title,
                                      Opportunity.status.in_(["detected", "micro_test", "validated"]))
        )).scalars().first()
        if existing is None:
            opp = Opportunity(
                user_id=user.id, title=title,
                description=top[0]["content"],
                buyer=top[0]["buyer"], problem=top[0]["problem"],
                evidence_json=[{"title": s["title"], "evidence_level": s["evidence_level"],
                                "strength": s["strength"], "url": s["url"]} for s in top[:5]],
                attention_score=max((s["strength"] for s in signals if s["evidence_level"] == "attention"),
                                    default=0.0),
                pain_score=max((s["strength"] for s in signals if s["evidence_level"] == "pain"), default=0.0),
                commercial_intent_score=max((s["strength"] for s in signals
                                             if s["evidence_level"] == "commercial_intent"), default=0.0),
                confidence=min(0.7, sum(s["strength"] for s in signals) / max(len(signals), 1)),
                status="detected",
            )
            db.add(opp)
            await db.flush()
            opportunity_id = opp.id
            await ledger.record(db, user.id, "opportunity_detected", actor_type="agent",
                                actor_id="market_radar", entity_type="opportunity", entity_id=opp.id,
                                payload={"title": opp.title})
        else:
            opportunity_id = existing.id

    outcome = "NO MATERIAL CHANGE" if opportunity_id is None else "OPPORTUNITY DETECTED"
    await ledger.record(db, user.id, "market_scan_completed", actor_type="agent",
                        actor_id="market_radar",
                        payload={"outcome": outcome, "signals": len(signals), "source": scan_source})
    await db.flush()
    return {"outcome": outcome, "signals": len(signals), "opportunity_id": opportunity_id,
            "verdict": verdict}
