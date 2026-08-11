from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from backend.billing import entitlements
from backend.core.models import MarketSignal, Opportunity
from backend.core.security import DB, CurrentUser
from backend.market.challenger import run_challenger
from backend.market.radar import run_market_scan

router = APIRouter()


def _serialize_opp(o: Opportunity) -> dict:
    return {
        "id": o.id, "title": o.title, "description": o.description, "buyer": o.buyer,
        "problem": o.problem, "evidence": o.evidence_json, "status": o.status,
        "scores": {
            "attention": o.attention_score, "pain": o.pain_score,
            "commercial_intent": o.commercial_intent_score, "competition": o.competition_score,
            "strategic_fit": o.strategic_fit_score, "time_to_market": o.time_to_market_score,
        },
        "estimated_test_cost_cents": o.estimated_test_cost_cents, "currency": o.currency,
        "confidence": o.confidence, "created_at": o.created_at,
    }


@router.post("/market/scan")
async def scan(user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "market_radar")
    result = await run_market_scan(db, user)
    await db.commit()
    return result


@router.get("/market/signals")
async def signals(user: CurrentUser, db: DB):
    rows = (await db.execute(
        select(MarketSignal).where(MarketSignal.user_id == user.id)
        .order_by(MarketSignal.created_at.desc()).limit(100)
    )).scalars()
    return [
        {"id": s.id, "source": s.source, "content": s.content, "url": s.url,
         "evidence_level": s.evidence_level, "strength": s.strength,
         "metadata": s.metadata_json, "created_at": s.created_at}
        for s in rows
    ]


@router.post("/market/challenge")
async def challenge(user: CurrentUser, db: DB):
    await entitlements.require_feature(db, user.id, "challenger")
    result = await run_challenger(db, user)
    await db.commit()
    return result


@router.get("/opportunities")
async def list_opportunities(user: CurrentUser, db: DB, status: str | None = None):
    q = select(Opportunity).where(Opportunity.user_id == user.id)
    if status:
        q = q.where(Opportunity.status == status)
    rows = (await db.execute(q.order_by(Opportunity.created_at.desc()).limit(100))).scalars()
    return [_serialize_opp(o) for o in rows]


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(opportunity_id: str, user: CurrentUser, db: DB):
    o = (await db.execute(select(Opportunity).where(
        Opportunity.id == opportunity_id, Opportunity.user_id == user.id
    ))).scalar_one_or_none()
    if o is None:
        raise HTTPException(404, "opportunity not found")
    return _serialize_opp(o)


@router.post("/opportunities/{opportunity_id}/ignore")
async def ignore_opportunity(opportunity_id: str, user: CurrentUser, db: DB):
    o = (await db.execute(select(Opportunity).where(
        Opportunity.id == opportunity_id, Opportunity.user_id == user.id
    ))).scalar_one_or_none()
    if o is None:
        raise HTTPException(404, "opportunity not found")
    o.status = "rejected"
    await db.commit()
    return _serialize_opp(o)


@router.get("/market/reports")
async def market_reports(user: CurrentUser, db: DB, instruction_id: str | None = None,
                         limit: int = 20):
    """Stored Market Watch reports, newest first (third pass §35)."""
    from backend.core.models import MarketReport

    q = (select(MarketReport).where(MarketReport.user_id == user.id)
         .order_by(MarketReport.created_at.desc()).limit(min(limit, 100)))
    if instruction_id:
        q = q.where(MarketReport.instruction_id == instruction_id)
    return [
        {"id": r.id, "instruction_id": r.instruction_id, "status": r.status,
         "sentiment": r.sentiment, "summary": r.summary_json, "sources": r.sources_json,
         "sample": r.sample_json, "query": r.query_json, "delivered": r.delivered_json,
         "crew_run_id": r.crew_run_id, "created_at": r.created_at}
        for r in (await db.execute(q)).scalars()
    ]
