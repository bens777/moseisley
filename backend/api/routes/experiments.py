from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.billing import entitlements
from backend.core.models import Experiment
from backend.core.security import DB, CurrentUser
from backend.experiments import service as experiments

router = APIRouter(prefix="/experiments")


def _serialize(e: Experiment) -> dict:
    return {
        "id": e.id, "hypothesis": e.hypothesis, "expected_result": e.expected_result,
        "metric": e.metric, "deadline": e.deadline,
        "cash_budget_cents": e.cash_budget_cents, "currency": e.currency,
        "human_time_budget_minutes": e.human_time_budget_minutes,
        "success_criterion": e.success_criterion, "kill_criterion": e.kill_criterion,
        "status": e.status, "result": e.result_json,
        "opportunity_id": e.opportunity_id, "project_id": e.project_id,
        "started_at": e.started_at, "stopped_at": e.stopped_at, "created_at": e.created_at,
    }


class CreateExperimentRequest(BaseModel):
    hypothesis: str
    metric: str | None = None
    expected_result: str | None = None
    deadline: str | None = None
    cash_budget_cents: int = 0
    currency: str = "EUR"
    human_time_budget_minutes: int = 0
    success_criterion: str
    kill_criterion: str
    opportunity_id: str | None = None
    project_id: str | None = None
    prediction_probability: float | None = None


@router.get("")
async def list_experiments(user: CurrentUser, db: DB, status: str | None = None):
    q = select(Experiment).where(Experiment.user_id == user.id)
    if status:
        q = q.where(Experiment.status == status)
    rows = (await db.execute(q.order_by(Experiment.created_at.desc()).limit(100))).scalars()
    return [_serialize(e) for e in rows]


@router.post("")
async def create(body: CreateExperimentRequest, user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "experiments")
    try:
        exp = await experiments.create_experiment(db, user.id, **body.model_dump())
    except experiments.ExperimentError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return _serialize(exp)


@router.post("/{experiment_id}/start")
async def start(experiment_id: str, user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "experiments")
    try:
        exp = await experiments.start_experiment(db, user.id, experiment_id)
    except experiments.ExperimentError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return _serialize(exp)


class StopRequest(BaseModel):
    outcome: str  # succeeded | killed | inconclusive
    observed_value: float | None = None
    notes: str | None = None


@router.post("/{experiment_id}/stop")
async def stop(experiment_id: str, body: StopRequest, user: CurrentUser, db: DB):
    try:
        exp = await experiments.stop_experiment(db, user.id, experiment_id,
                                                outcome=body.outcome,
                                                observed_value=body.observed_value,
                                                notes=body.notes)
    except experiments.ExperimentError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return _serialize(exp)
