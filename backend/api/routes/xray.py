from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.billing import entitlements
from backend.core.models import XRayFinding, XRayRun
from backend.core.security import DB, CurrentUser
from backend.xray.engine import run_xray

router = APIRouter(prefix="/xray")


def _serialize_finding(f: XRayFinding) -> dict:
    return {
        "id": f.id, "run_id": f.run_id, "type": f.type, "title": f.title,
        "description": f.description, "evidence": f.evidence_json, "confidence": f.confidence,
        "value_type": f.value_type, "estimated_value_cents": f.estimated_value_cents,
        "estimated_time_minutes": f.estimated_time_minutes, "currency": f.currency,
        "verified": f.verified, "recommended_action": f.recommended_action,
        "risk_level": f.risk_level, "source_references": f.source_references_json,
        "status": f.status, "created_at": f.created_at,
    }


def _serialize_run(r: XRayRun) -> dict:
    return {
        "id": r.id, "horizon_days": r.horizon_days, "status": r.status,
        "started_at": r.started_at, "completed_at": r.completed_at,
        "summary": r.summary_json, "error": r.error, "created_at": r.created_at,
    }


class RunRequest(BaseModel):
    horizon_days: int = 90


@router.post("/run")
async def run(body: RunRequest, user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "xray")
    if body.horizon_days not in (30, 60, 90):
        raise HTTPException(400, "horizon_days must be 30, 60 or 90")
    run = await run_xray(db, user.id, body.horizon_days)
    await db.commit()
    return _serialize_run(run)


@router.get("/runs")
async def list_runs(user: CurrentUser, db: DB):
    rows = (await db.execute(
        select(XRayRun).where(XRayRun.user_id == user.id).order_by(XRayRun.created_at.desc()).limit(20)
    )).scalars()
    return [_serialize_run(r) for r in rows]


@router.get("/latest")
async def latest(user: CurrentUser, db: DB):
    run = (await db.execute(
        select(XRayRun).where(XRayRun.user_id == user.id, XRayRun.status == "completed")
        .order_by(XRayRun.created_at.desc()).limit(1)
    )).scalars().first()
    if run is None:
        return {"run": None, "findings": {}, "no_verified_money_message": None}
    rows = (await db.execute(
        select(XRayFinding).where(XRayFinding.run_id == run.id).order_by(XRayFinding.created_at)
    )).scalars()
    grouped: dict[str, list] = {}
    for f in rows:
        grouped.setdefault(f.type, []).append(_serialize_finding(f))
    message = None
    if run.summary_json.get("no_verified_money"):
        message = "No verified recoverable money found."
    return {"run": _serialize_run(run), "findings": grouped, "no_verified_money_message": message}


class FindingUpdate(BaseModel):
    status: str  # open | actioned | dismissed


@router.patch("/findings/{finding_id}")
async def update_finding(finding_id: str, body: FindingUpdate, user: CurrentUser, db: DB):
    if body.status not in ("open", "actioned", "dismissed"):
        raise HTTPException(400, "invalid status")
    f = (await db.execute(select(XRayFinding).where(
        XRayFinding.id == finding_id, XRayFinding.user_id == user.id
    ))).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, "finding not found")
    f.status = body.status
    await db.commit()
    return _serialize_finding(f)
