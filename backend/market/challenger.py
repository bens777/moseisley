"""Challenger (§59): tries to prove the current strategy wrong.

The Challenger can recommend and propose experiments; it can never execute privileged
actions, change strategy state, or bypass hysteresis. Its output is advice.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch
from backend.core.models import MarketSignal, User
from backend.documents import service as documents
from backend.life_kernel import world_model
from backend.providers import registry
from backend.providers.clients import ProviderError
from backend.providers.registry import LlmBudgetExceeded, NoProviderAvailable

CHALLENGER_PROMPT = """You are the Challenger agent of the user's AI crew on Moseisley.sh. Your explicit mission:
try to prove the user's current strategy is wrong.

Look for: confirmation bias, sunk-cost fallacy, missing data, opportunity cost,
overconfidence, inadequate execution volume, premature abandonment, market regime change.

Current state:
{context}

Recent market signals:
{signals}

Reply ONLY with JSON:
{{"verdict": "hold" | "challenge",
  "arguments": ["..."],
  "missing_data": ["..."],
  "proposed_micro_tests": [{{"hypothesis": "...", "metric": "...", "max_cash_eur": 0,
                             "max_hours": 0, "success": "...", "kill": "..."}}],
  "confidence": 0.0}}

Rules: "challenge" requires concrete arguments grounded in the provided state — not
generic advice. Proposing a micro test is the strongest action you may take; you cannot
change or kill the current strategy."""


async def run_challenger(db: AsyncSession, user: User) -> dict:
    await killswitch.require_off(db, user.id, killswitch.PAUSE_ALL_AGENTS)
    snapshot = await world_model.snapshot(db, user.id)
    signals = list((await db.execute(
        select(MarketSignal).where(MarketSignal.user_id == user.id)
        .order_by(MarketSignal.created_at.desc()).limit(10)
    )).scalars())
    signal_text = "\n".join(
        f"- [{s.evidence_level} {s.strength:.1f}] {s.content[:200]}" for s in signals
    ) or "none"

    result_dict: dict
    try:
        result = await registry.generate(db, user.id, [
            {"role": "system", "content": "You are a rigorous internal red team. JSON only."},
            {"role": "user", "content": CHALLENGER_PROMPT.format(
                context=json.dumps(snapshot, default=str)[:6000], signals=signal_text)},
        ], crew_role="challenger", purpose="audit", json_mode=True, max_tokens=1200)
        parsed = result.parse_json()
        if not isinstance(parsed, dict) or "verdict" not in parsed:
            raise ValueError("bad challenger output")
        result_dict = {
            "verdict": "challenge" if parsed.get("verdict") == "challenge" else "hold",
            "arguments": [str(a)[:500] for a in (parsed.get("arguments") or [])][:8],
            "missing_data": [str(a)[:300] for a in (parsed.get("missing_data") or [])][:8],
            "proposed_micro_tests": (parsed.get("proposed_micro_tests") or [])[:3],
            "confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0))),
            "source": "llm",
        }
    except (NoProviderAvailable, LlmBudgetExceeded, ProviderError, ValueError):
        result_dict = {
            "verdict": "hold",
            "arguments": ["No LLM provider available — challenge deferred."],
            "missing_data": [], "proposed_micro_tests": [], "confidence": 0.0,
            "source": "unavailable",
        }

    lines = [f"# Challenger — verdict: {result_dict['verdict'].upper()}", ""]
    lines += [f"- {a}" for a in result_dict["arguments"]]
    if result_dict["proposed_micro_tests"]:
        lines += ["", "## Proposed micro tests"]
        for t in result_dict["proposed_micro_tests"]:
            lines.append(f"- {t.get('hypothesis', '?')} (≤€{t.get('max_cash_eur', 0)}, "
                         f"≤{t.get('max_hours', 0)}h)")
    await documents.upsert_document(db, user.id, "/reports/challenger.md",
                                    "\n".join(lines) + "\n", actor_type="system")
    await db.flush()
    return result_dict
