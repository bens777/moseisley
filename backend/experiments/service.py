"""Experiment Engine (§71, §125): opportunities earn resources progressively.

Every experiment carries explicit budgets and falsifiable success/kill criteria.
Stage transitions are recorded in the Ledger; results feed the Auditor.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Experiment, Opportunity, Prediction
from backend.ledger import service as ledger


class ExperimentError(Exception):
    pass


async def create_experiment(
    db: AsyncSession, user_id: str, *,
    hypothesis: str, metric: str | None = None, expected_result: str | None = None,
    deadline: str | None = None, cash_budget_cents: int = 0, currency: str = "EUR",
    human_time_budget_minutes: int = 0, success_criterion: str = "",
    kill_criterion: str = "", opportunity_id: str | None = None, project_id: str | None = None,
    prediction_probability: float | None = None,
) -> Experiment:
    if cash_budget_cents < 0 or human_time_budget_minutes < 0:
        raise ExperimentError("budgets must be non-negative")
    if not success_criterion or not kill_criterion:
        raise ExperimentError("success and kill criteria are required")
    if opportunity_id:
        opp = (await db.execute(select(Opportunity).where(
            Opportunity.id == opportunity_id, Opportunity.user_id == user_id
        ))).scalar_one_or_none()
        if opp is None:
            raise ExperimentError("opportunity not found")
    exp = Experiment(
        user_id=user_id, hypothesis=hypothesis, metric=metric, expected_result=expected_result,
        deadline=deadline, cash_budget_cents=cash_budget_cents, currency=currency,
        human_time_budget_minutes=human_time_budget_minutes,
        success_criterion=success_criterion, kill_criterion=kill_criterion,
        opportunity_id=opportunity_id, project_id=project_id, status="draft",
    )
    db.add(exp)
    await db.flush()
    await ledger.record(db, user_id, "experiment_created", entity_type="experiment", entity_id=exp.id,
                        payload={"hypothesis": hypothesis, "cash_budget_cents": cash_budget_cents,
                                 "success": success_criterion, "kill": kill_criterion})
    if prediction_probability is not None:
        pred = Prediction(user_id=user_id, experiment_id=exp.id,
                          statement=expected_result or hypothesis,
                          probability=prediction_probability, metric=metric, deadline=deadline)
        db.add(pred)
        await db.flush()
        await ledger.record(db, user_id, "prediction_created", entity_type="prediction",
                            entity_id=pred.id,
                            payload={"experiment_id": exp.id, "probability": prediction_probability})
    return exp


async def get_experiment(db: AsyncSession, user_id: str, experiment_id: str) -> Experiment:
    exp = (await db.execute(select(Experiment).where(
        Experiment.id == experiment_id, Experiment.user_id == user_id
    ))).scalar_one_or_none()
    if exp is None:
        raise ExperimentError("experiment not found")
    return exp


async def start_experiment(db: AsyncSession, user_id: str, experiment_id: str) -> Experiment:
    exp = await get_experiment(db, user_id, experiment_id)
    if exp.status != "draft":
        raise ExperimentError(f"cannot start experiment in status {exp.status}")
    exp.status = "running"
    exp.started_at = datetime.now(UTC)
    if exp.opportunity_id:
        opp = (await db.execute(select(Opportunity).where(Opportunity.id == exp.opportunity_id))
               ).scalar_one_or_none()
        if opp is not None and opp.status == "detected":
            opp.status = "micro_test"
    await ledger.record(db, user_id, "experiment_started", entity_type="experiment",
                        entity_id=exp.id, payload={"hypothesis": exp.hypothesis})
    await db.flush()
    return exp


async def stop_experiment(
    db: AsyncSession, user_id: str, experiment_id: str, *,
    outcome: str, observed_value: float | None = None, notes: str | None = None,
) -> Experiment:
    """outcome: succeeded | killed | inconclusive — judged against predeclared criteria."""
    if outcome not in ("succeeded", "killed", "inconclusive"):
        raise ExperimentError("invalid outcome")
    exp = await get_experiment(db, user_id, experiment_id)
    if exp.status != "running":
        raise ExperimentError(f"cannot stop experiment in status {exp.status}")
    exp.status = outcome
    exp.stopped_at = datetime.now(UTC)
    exp.result_json = {"observed_value": observed_value, "notes": notes, "outcome": outcome}
    if exp.opportunity_id:
        opp = (await db.execute(select(Opportunity).where(Opportunity.id == exp.opportunity_id))
               ).scalar_one_or_none()
        if opp is not None:
            opp.status = {"succeeded": "validated", "killed": "rejected"}.get(outcome, opp.status)
    # resolve linked predictions via outcome records
    preds = list((await db.execute(select(Prediction).where(
        Prediction.experiment_id == exp.id, Prediction.status == "open"
    ))).scalars())
    from backend.core.models import Outcome

    for p in preds:
        p.status = "resolved"
        db.add(Outcome(user_id=user_id, prediction_id=p.id, observed_value=observed_value,
                       observed_text=notes, source="experiment"))
    await ledger.record(db, user_id, "experiment_stopped", entity_type="experiment",
                        entity_id=exp.id,
                        payload={"outcome": outcome, "observed_value": observed_value})
    if preds:
        await ledger.record(db, user_id, "outcome_recorded", entity_type="experiment",
                            entity_id=exp.id, payload={"predictions_resolved": len(preds)})
    await db.flush()
    return exp
