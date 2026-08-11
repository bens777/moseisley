"""X-Ray run orchestration (§41, §50, §120)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Goal, XRayFinding, XRayRun
from backend.ledger import service as ledger
from backend.xray import analyzers, ingest

logger = logging.getLogger("mychief.xray")


async def run_xray(db: AsyncSession, user_id: str, horizon_days: int = 90) -> XRayRun:
    run = XRayRun(user_id=user_id, horizon_days=horizon_days, status="running",
                  started_at=datetime.now(UTC))
    db.add(run)
    await db.flush()
    await ledger.record(db, user_id, "xray_started", entity_type="xray_run", entity_id=run.id,
                        payload={"horizon_days": horizon_days})
    try:
        emails, events = await ingest.ingest(db, user_id, horizon_days)

        stated_priority = None
        goal = (await db.execute(
            select(Goal).where(Goal.user_id == user_id, Goal.status == "active").order_by(Goal.created_at)
        )).scalars().first()
        if goal is not None:
            metric = goal.metric.lower()
            if any(w in metric for w in ("income", "revenue", "customer", "client", "sales", "mrr")):
                stated_priority = "sales"

        findings: list[dict] = []
        findings += analyzers.found_money(emails)
        findings += analyzers.estimated_opportunity(emails)
        findings += analyzers.lost_commitments(emails)
        findings += analyzers.found_time(emails, events, horizon_days)
        findings += analyzers.goal_drift(events, stated_priority)
        findings += analyzers.automatable_work(emails)
        findings += analyzers.shadow_backtest(findings, horizon_days)

        for f in findings:
            db.add(XRayFinding(
                user_id=user_id, run_id=run.id, type=f["type"], title=f["title"],
                description=f.get("description", ""),
                evidence_json=f.get("evidence", []) + ([f["extra"]] if f.get("extra") else []),
                confidence=f.get("confidence", 0.5),
                value_type=f.get("value_type"),
                estimated_value_cents=f.get("estimated_value_cents"),
                estimated_time_minutes=f.get("estimated_time_minutes"),
                verified=bool(f.get("verified")),
                recommended_action=f.get("recommended_action"),
                risk_level=int(f.get("risk_level", 0)),
                source_references_json=f.get("source_references", []),
            ))

        verified_cents = sum(f.get("estimated_value_cents") or 0 for f in findings
                             if f["type"] == "found_money" and f.get("verified"))
        estimated_cents = sum(f.get("estimated_value_cents") or 0 for f in findings
                              if f["type"] == "estimated_opportunity")
        time_minutes = sum(f.get("estimated_time_minutes") or 0 for f in findings)

        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.summary_json = {
            "emails_analyzed": len(emails),
            "events_analyzed": len(events),
            "findings": len(findings),
            "verified_money_cents": verified_cents,
            "estimated_opportunity_cents": estimated_cents,
            "estimated_time_recoverable_minutes": time_minutes,
            "no_verified_money": verified_cents == 0,
        }
        await ledger.record(db, user_id, "xray_completed", entity_type="xray_run", entity_id=run.id,
                            payload=run.summary_json)
        await db.flush()
    except Exception as e:
        logger.exception("xray run failed")
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
        run.completed_at = datetime.now(UTC)
        await db.flush()
    return run
