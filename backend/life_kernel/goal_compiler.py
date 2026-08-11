"""Goal Compiler (§11): natural language → structured Goal.

LLM extracts; deterministic code validates, asks at most one concise follow-up for a
missing critical field, and records the Ledger event on acceptance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Goal
from backend.ledger import service as ledger
from backend.life_kernel.focus import rebuild_focus
from backend.providers import registry

SYSTEM_PROMPT = """You extract a structured goal from a user's statement.
Return ONLY a JSON object with fields:
  metric (snake_case string), title (short human phrase), target (number or null),
  unit (string or null, e.g. "EUR", "hours", "customers"), currency (ISO 4217 or null),
  deadline (YYYY-MM-DD or null), constraints (object, may be empty),
  missing_critical (array of field names among ["metric","target"] that could not be determined).
Do not invent numbers the user did not state. Constraints example: {"max_weekly_work_hours": 30}.
"""


@dataclass
class CompileResult:
    status: str  # "created" | "needs_clarification" | "error"
    goal: Goal | None = None
    question: str | None = None
    extracted: dict | None = None


_FOLLOW_UPS = {
    "metric": "What single metric should this goal track (for example: monthly independent income)?",
    "target": "What is the target number for this goal?",
}


def _validate(extracted: dict) -> list[str]:
    # Missing-field detection is deterministic from actual values; the model's
    # missing_critical list is advisory only.
    missing = []
    if not extracted.get("metric"):
        missing.append("metric")
    if extracted.get("target") is None:
        missing.append("target")
    # deterministic sanity checks
    if extracted.get("deadline") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(extracted["deadline"])):
        extracted["deadline"] = None
    if extracted.get("currency") and not re.fullmatch(r"[A-Z]{3}", str(extracted["currency"])):
        extracted["currency"] = None
    return sorted(set(missing), key=["metric", "target"].index)


async def compile_goal(
    db: AsyncSession, user_id: str, text: str, *, prior_extracted: dict | None = None
) -> CompileResult:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if prior_extracted:
        messages.append(
            {"role": "system",
             "content": f"Previously extracted (merge with the new answer): {prior_extracted}"}
        )
    messages.append({"role": "user", "content": text})
    result = await registry.complete(db, user_id, "goal_compilation", messages, json_mode=True)
    extracted = result.parse_json()
    if not isinstance(extracted, dict):
        return CompileResult(status="error", question="I couldn't parse that goal. Could you rephrase it?")
    if prior_extracted:
        merged = dict(prior_extracted)
        for k, v in extracted.items():
            if v not in (None, "", [], {}):
                merged[k] = v
        extracted = merged

    missing = _validate(extracted)
    if missing:
        return CompileResult(
            status="needs_clarification", question=_FOLLOW_UPS[missing[0]], extracted=extracted
        )

    goal = Goal(
        user_id=user_id,
        title=str(extracted.get("title") or extracted["metric"].replace("_", " ").title()),
        metric=str(extracted["metric"]),
        target_value=float(extracted["target"]),
        unit=extracted.get("unit"),
        currency=extracted.get("currency"),
        deadline=extracted.get("deadline"),
        constraints_json=extracted.get("constraints") or {},
    )
    db.add(goal)
    await db.flush()
    await ledger.record(
        db, user_id, "goal_created", actor_type="user", entity_type="goal", entity_id=goal.id,
        payload={"metric": goal.metric, "target": goal.target_value, "deadline": goal.deadline,
                 "constraints": goal.constraints_json},
    )
    await rebuild_focus(db, user_id)
    return CompileResult(status="created", goal=goal, extracted=extracted)
