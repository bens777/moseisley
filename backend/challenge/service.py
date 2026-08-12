"""What the public page reads. Read-only, no auth, no user data."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.challenge import data as feed
from backend.challenge.engine import LEGAL_NOTICE, START_CENTS
from backend.core.models import ChallengeDecision, ChallengeSnapshot

MAX_DECISIONS = 200


def _stats(snapshots: list[ChallengeSnapshot], decisions: list[ChallengeDecision]) -> dict:
    equity = [s.equity_cents for s in snapshots] or [START_CENTS]
    current = equity[-1]

    peak = equity[0]
    max_drawdown = Decimal(0)
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            drop = (Decimal(peak - value) / Decimal(peak)) * 100
            max_drawdown = max(max_drawdown, drop)

    closed = [d for d in decisions if d.action == "sell" and d.realized_pnl_cents is not None]
    wins = [d for d in closed if d.realized_pnl_cents > 0]
    return {
        "start_cents": START_CENTS,
        "equity_cents": current,
        "pnl_cents": current - START_CENTS,
        "pnl_pct": round(float(Decimal(current - START_CENTS) / START_CENTS * 100), 2),
        "closed_trades": len(closed),
        "wins": len(wins),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "max_drawdown_pct": round(float(max_drawdown), 2),
        "realized_pnl_cents": sum(d.realized_pnl_cents for d in closed),
    }


def _decision(d: ChallengeDecision) -> dict:
    return {
        "date": d.trade_date, "symbol": d.symbol, "action": d.action,
        "reason": d.reason, "price": d.price, "units": d.units,
        "box_top": d.box_top, "box_bottom": d.box_bottom, "stop": d.stop,
        "equity_cents_after": d.equity_cents_after,
        "realized_pnl_cents": d.realized_pnl_cents,
    }


async def public_state(db: AsyncSession) -> dict:
    snapshots = list((await db.execute(
        select(ChallengeSnapshot).order_by(ChallengeSnapshot.trade_date))).scalars())
    decisions = list((await db.execute(
        select(ChallengeDecision).order_by(ChallengeDecision.trade_date.desc(),
                                           ChallengeDecision.symbol)
        .limit(MAX_DECISIONS))).scalars())
    all_decisions = list((await db.execute(select(ChallengeDecision))).scalars())
    latest = snapshots[-1] if snapshots else None

    return {
        "legal": LEGAL_NOTICE,
        "status": latest.status if latest else "not_started",
        "note": latest.note if latest else "",
        "as_of": latest.trade_date if latest else None,
        "method": {
            "name": "Darvas box",
            "rules": [
                "A box top is a high that survives 3 sessions without being exceeded.",
                "A box bottom is the low made after it, surviving 3 sessions.",
                "Buy when a close breaks above the box top.",
                "Stop-loss sits at the box bottom and only ever moves up.",
                "Each higher box that confirms trails the stop up to its bottom.",
            ],
            "decided_by": "Deterministic code. No language model is in the trading loop.",
        },
        "watchlist": [{"symbol": i.symbol, "name": i.name} for i in feed.WATCHLIST],
        "stats": _stats(snapshots, all_decisions),
        "equity_curve": [{"date": s.trade_date, "equity_cents": s.equity_cents,
                          "status": s.status} for s in snapshots],
        "positions": (latest.positions_json if latest else []) or [],
        "decisions": [_decision(d) for d in decisions],
        "decision_count": len(all_decisions),
    }
