"""Auditor (§63, §128): logically independent from the Strategist.

Works from raw ledger/DB evidence — never from the Strategist's narrative.
Deterministic arithmetic for errors and calibration; no LLM required.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import (
    Event,
    Experiment,
    Outcome,
    Prediction,
    SpendIntent,
    User,
)
from backend.documents import service as documents
from backend.ledger import service as ledger

MEANINGFUL_RELATIVE_ERROR = 0.5  # observed off by >50% of target is a meaningful miss


def prediction_error(target: float, observed: float) -> dict:
    error = observed - target
    relative = abs(error) / abs(target) if target else None
    return {
        "target": target, "observed": observed, "error": error,
        "relative_error": relative,
        "meaningful_miss": relative is not None and relative > MEANINGFUL_RELATIVE_ERROR,
    }


async def audit_predictions(db: AsyncSession, user_id: str) -> dict:
    """Link resolved predictions to outcomes; compute forecast errors + calibration."""
    predictions = list((await db.execute(
        select(Prediction).where(Prediction.user_id == user_id, Prediction.status == "resolved")
    )).scalars())
    reviews = []
    brier_terms = []
    for p in predictions:
        outcome = (await db.execute(
            select(Outcome).where(Outcome.prediction_id == p.id).order_by(Outcome.observed_at.desc())
        )).scalars().first()
        if outcome is None:
            continue
        entry: dict = {"prediction_id": p.id, "statement": p.statement,
                       "probability": p.probability}
        if p.target_value is not None and outcome.observed_value is not None:
            entry.update(prediction_error(p.target_value, outcome.observed_value))
            hit = outcome.observed_value >= p.target_value
            entry["hit"] = hit
            if p.probability is not None:
                brier_terms.append((p.probability - (1.0 if hit else 0.0)) ** 2)
        reviews.append(entry)
    calibration = {
        "resolved_predictions": len(reviews),
        "meaningful_misses": sum(1 for r in reviews if r.get("meaningful_miss")),
        "brier_score": round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None,
        "hit_rate": (round(sum(1 for r in reviews if r.get("hit")) /
                           max(sum(1 for r in reviews if "hit" in r), 1), 3)
                     if any("hit" in r for r in reviews) else None),
        "avg_stated_probability": (round(sum(p.probability for p in predictions
                                             if p.probability is not None) /
                                         max(sum(1 for p in predictions
                                                 if p.probability is not None), 1), 3)
                                   if any(p.probability is not None for p in predictions) else None),
    }
    return {"reviews": reviews, "calibration": calibration}


async def audit_experiments(db: AsyncSession, user_id: str) -> dict:
    """Budget vs spend and criteria discipline for stopped experiments."""
    experiments = list((await db.execute(
        select(Experiment).where(Experiment.user_id == user_id,
                                 Experiment.status.in_(["succeeded", "killed", "inconclusive"]))
    )).scalars())
    entries = []
    for e in experiments:
        spent = 0
        intents = list((await db.execute(
            select(SpendIntent).where(SpendIntent.experiment_id == e.id,
                                      SpendIntent.status == "executed")
        )).scalars())
        spent = sum(i.amount_cents for i in intents)
        entries.append({
            "experiment_id": e.id, "hypothesis": e.hypothesis, "outcome": e.status,
            "cash_budget_cents": e.cash_budget_cents, "spent_cents": spent,
            "over_budget": spent > e.cash_budget_cents if e.cash_budget_cents else False,
            "observed_value": (e.result_json or {}).get("observed_value"),
        })
    return {
        "experiments_evaluated": len(entries),
        "over_budget": sum(1 for x in entries if x["over_budget"]),
        "entries": entries,
    }


async def audit_action_truthfulness(db: AsyncSession, user_id: str, days: int = 7) -> dict:
    """Claimed action vs recorded execution status (§112): count FAILED/UNKNOWN."""
    since = datetime.now(UTC) - timedelta(days=days)
    events = list((await db.execute(
        select(Event).where(Event.user_id == user_id, Event.event_type == "tool_executed",
                            Event.created_at >= since)
    )).scalars())
    statuses: dict[str, int] = {}
    for e in events:
        s = (e.payload_json or {}).get("status", "UNKNOWN")
        statuses[s] = statuses.get(s, 0) + 1
    return {"tool_executions": len(events), "by_status": statuses,
            "unverified": statuses.get("UNKNOWN", 0), "failed": statuses.get("FAILED", 0)}


async def run_weekly_review(db: AsyncSession, user: User) -> dict:
    predictions = await audit_predictions(db, user.id)
    experiments = await audit_experiments(db, user.id)
    actions = await audit_action_truthfulness(db, user.id)

    cal = predictions["calibration"]
    lines = [f"# Weekly Review — {datetime.now(UTC).date().isoformat()}", ""]
    lines.append("## Prediction calibration")
    lines.append(f"- Resolved predictions: {cal['resolved_predictions']}")
    lines.append(f"- Meaningful misses (>50% off): {cal['meaningful_misses']}")
    if cal["brier_score"] is not None:
        lines.append(f"- Brier score: {cal['brier_score']} (lower is better)")
    if cal["hit_rate"] is not None and cal["avg_stated_probability"] is not None:
        gap = cal["avg_stated_probability"] - cal["hit_rate"]
        lines.append(f"- Stated confidence {cal['avg_stated_probability']:.0%} vs "
                     f"hit rate {cal['hit_rate']:.0%}"
                     + (" — **overconfident**" if gap > 0.15 else ""))
    for r in predictions["reviews"]:
        if r.get("meaningful_miss"):
            lines.append(f"  - MISS: “{r['statement']}” — target {r['target']:g}, "
                         f"observed {r['observed']:g}")
    lines.append("")
    lines.append("## Experiments")
    lines.append(f"- Evaluated: {experiments['experiments_evaluated']}, "
                 f"over budget: {experiments['over_budget']}")
    lines.append("")
    lines.append("## Action truthfulness")
    lines.append(f"- Tool executions: {actions['tool_executions']} "
                 f"(failed: {actions['failed']}, unverified: {actions['unverified']})")

    await documents.upsert_document(db, user.id, "/reports/weekly-review.md",
                                    "\n".join(lines) + "\n", actor_type="system")
    await ledger.record(db, user.id, "audit_completed", actor_type="agent", actor_id="auditor",
                        payload={"predictions_reviewed": cal["resolved_predictions"],
                                 "meaningful_misses": cal["meaningful_misses"]})
    await db.flush()
    return {
        "predictions_reviewed": cal["resolved_predictions"],
        "calibration": cal,
        "experiments": experiments,
        "actions": actions,
        "reviews": predictions["reviews"],
    }
