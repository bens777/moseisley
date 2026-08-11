from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.models import Decision, Outcome, Prediction
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger

router = APIRouter()


class DecisionRequest(BaseModel):
    goal_id: str | None = None
    reason: str
    alternatives: list[str] = []
    selected_action: str
    confidence: float | None = None


@router.post("/decisions")
async def create_decision(body: DecisionRequest, user: CurrentUser, db: DB):
    d = Decision(user_id=user.id, goal_id=body.goal_id, reason=body.reason,
                 alternatives_json=body.alternatives, selected_action=body.selected_action,
                 confidence=body.confidence)
    db.add(d)
    await db.flush()
    await ledger.record(db, user.id, "decision_recorded", entity_type="decision", entity_id=d.id,
                        payload={"selected_action": body.selected_action})
    await db.commit()
    return {"id": d.id}


@router.get("/decisions")
async def list_decisions(user: CurrentUser, db: DB):
    rows = (await db.execute(
        select(Decision).where(Decision.user_id == user.id).order_by(Decision.created_at.desc()).limit(100)
    )).scalars()
    return [
        {"id": d.id, "goal_id": d.goal_id, "reason": d.reason, "alternatives": d.alternatives_json,
         "selected_action": d.selected_action, "confidence": d.confidence, "created_at": d.created_at}
        for d in rows
    ]


class PredictionRequest(BaseModel):
    goal_id: str | None = None
    experiment_id: str | None = None
    decision_id: str | None = None
    statement: str
    probability: float | None = None
    metric: str | None = None
    target_value: float | None = None
    deadline: str | None = None


@router.post("/predictions")
async def create_prediction(body: PredictionRequest, user: CurrentUser, db: DB):
    p = Prediction(user_id=user.id, **body.model_dump())
    db.add(p)
    await db.flush()
    await ledger.record(db, user.id, "prediction_created", entity_type="prediction", entity_id=p.id,
                        payload={"statement": body.statement, "probability": body.probability})
    await db.commit()
    return {"id": p.id}


@router.get("/predictions")
async def list_predictions(user: CurrentUser, db: DB, status: str | None = None):
    q = select(Prediction).where(Prediction.user_id == user.id)
    if status:
        q = q.where(Prediction.status == status)
    rows = (await db.execute(q.order_by(Prediction.created_at.desc()).limit(200))).scalars()
    return [
        {"id": p.id, "statement": p.statement, "probability": p.probability, "metric": p.metric,
         "target_value": p.target_value, "deadline": p.deadline, "status": p.status,
         "goal_id": p.goal_id, "experiment_id": p.experiment_id, "created_at": p.created_at}
        for p in rows
    ]


class OutcomeRequest(BaseModel):
    prediction_id: str | None = None
    observed_value: float | None = None
    observed_text: str | None = None
    source: str = "manual"


@router.post("/outcomes")
async def record_outcome(body: OutcomeRequest, user: CurrentUser, db: DB):
    o = Outcome(user_id=user.id, **body.model_dump())
    db.add(o)
    await db.flush()
    if body.prediction_id:
        pred = (await db.execute(
            select(Prediction).where(Prediction.id == body.prediction_id, Prediction.user_id == user.id)
        )).scalar_one_or_none()
        if pred:
            pred.status = "resolved"
    await ledger.record(db, user.id, "outcome_recorded", entity_type="outcome", entity_id=o.id,
                        payload={"observed_value": body.observed_value, "prediction_id": body.prediction_id})
    await db.commit()
    return {"id": o.id}
